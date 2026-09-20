import importlib
import math
import os
import time
from threading import RLock


class BrokerUnavailable(Exception):
    pass


class DemoBroker:
    mode = "demo"
    lock = RLock()
    quotes = {"EURUSD": 1.1000, "GBPUSD": 1.2700, "AUDUSD": 0.6600, "NZDUSD": 0.6100, "USDJPY": 150.000}

    def symbols(self):
        return list(self.quotes)

    def context(self, symbol):
        if symbol not in self.quotes:
            raise ValueError("Symbol không có trong chế độ mô phỏng.")
        jpy = symbol == 'USDJPY'
        return {"mode": "demo", "account": {"login": "DEMO", "server": "Dữ liệu giả lập", "currency": "USD", "equity": 1000, "balance": 1000},
                "symbol": {"name": symbol, "bid": self.quotes[symbol] - (0.01 if jpy else 0.0001), "ask": self.quotes[symbol], "tick_size": 0.001 if jpy else 0.00001, "digits": 3 if jpy else 5, "contract_size": 100000, "volume_min": 0.01, "volume_step": 0.01, "volume_max": 100},
                "quote_time": None, "warnings": ["MÔ PHỎNG: giá cố định và tài khoản giả lập, không phải dữ liệu MT5."]}

    def profit(self, symbol, side, volume, entry, stop):
        pnl = (stop - entry) * 100000 * volume * (1 if side == "buy" else -1)
        return pnl / stop if symbol == 'USDJPY' else pnl

    def bars(self, symbol, timeframe, count):
        from .stops import FRAME_SECONDS
        seconds = FRAME_SECONDS[timeframe]
        end = int(time.time()) // seconds * seconds
        amplitude = (0.20 if symbol == 'USDJPY' else 0.002) * (seconds / 900) ** 0.25
        center = self.quotes[symbol]
        return [{'time':end-(count-i)*seconds,
                 'high':center+amplitude*math.sin(i*math.pi/6)+amplitude*.2,
                 'low':center+amplitude*math.sin(i*math.pi/6)-amplitude*.2} for i in range(count)]

    def verify_account(self, account):
        pass


class MT5Broker:
    mode = "mt5"

    def __init__(self):
        self.lock = RLock()
        self.mt5 = None

    def connect(self):
        if self.mt5 is None:
            try:
                self.mt5 = importlib.import_module("MetaTrader5")
            except ImportError as exc:
                raise BrokerUnavailable("Chưa cài MetaTrader5. Cần Python 64-bit trên Windows VPS.") from exc
        path = os.getenv("MT5_PATH", "")
        expected_login, expected_server = os.getenv("MT5_LOGIN"), os.getenv("MT5_SERVER")
        if not path or not expected_login or not expected_server:
            raise BrokerUnavailable("Cấu hình MT5_PATH, MT5_LOGIN và MT5_SERVER trong .env trước khi kết nối.")
        if not self.mt5.initialize(path, timeout=10000):
            raise BrokerUnavailable("Không kết nối được MT5. Kiểm tra terminal và phiên Windows đang chạy.")
        terminal, account = self.mt5.terminal_info(), self.mt5.account_info()
        if not terminal or not terminal.connected or not account:
            raise BrokerUnavailable("MT5 chưa kết nối broker hoặc chưa đăng nhập.")
        if str(account.login) != expected_login or account.server != expected_server:
            raise BrokerUnavailable("MT5 đang ở tài khoản/server khác cấu hình. Dừng tính để tránh nhầm dữ liệu.")
        return account

    def symbols(self):
        self.connect()
        symbols = self.mt5.symbols_get()
        if symbols is None:
            raise BrokerUnavailable("Không đọc được danh sách symbol từ MT5.")
        return sorted(s.name for s in symbols if s.trade_calc_mode in (0, 5))

    def context(self, symbol):
        account = self.connect()
        spec = self.mt5.symbol_info(symbol)
        if spec is None or spec.trade_calc_mode not in (0, 5):
            raise ValueError("Bản này chỉ hỗ trợ symbol có cách tính Forex/Forex No Leverage.")
        if not self.mt5.symbol_select(symbol, True):
            raise BrokerUnavailable("Không bật được symbol trong Market Watch.")
        tick = self.mt5.symbol_info_tick(symbol)
        max_age = float(os.getenv("MAX_TICK_AGE_SECONDS", "120"))
        if tick is None or not all(math.isfinite(x) and x > 0 for x in (tick.bid, tick.ask)):
            raise BrokerUnavailable("Chưa có báo giá hợp lệ cho symbol.")
        if time.time() - tick.time > max_age:
            raise BrokerUnavailable("Báo giá quá cũ (có thể thị trường đóng cửa). Không dùng để tính lot mới.")
        warnings = []
        if account.currency != "USD":
            warnings.append(f"Tài khoản dùng {account.currency}. Mọi số tiền theo đơn vị này, không tự quy đổi thành USD.")
        return {"mode": "mt5", "account": {"login": str(account.login), "server": account.server, "currency": account.currency, "equity": account.equity, "balance": account.balance},
                "symbol": {"name": spec.name, "bid": tick.bid, "ask": tick.ask, "tick_size": spec.trade_tick_size, "digits": spec.digits, "contract_size": spec.trade_contract_size, "volume_min": spec.volume_min, "volume_step": spec.volume_step, "volume_max": spec.volume_max},
                "quote_time": tick.time, "warnings": warnings}

    def profit(self, symbol, side, volume, entry, stop):
        value = self.mt5.order_calc_profit(self.mt5.ORDER_TYPE_BUY if side == "buy" else self.mt5.ORDER_TYPE_SELL, symbol, volume, entry, stop)
        if value is None or not math.isfinite(value):
            raise BrokerUnavailable("MT5 không tính được P/L. Kiểm tra symbol và các cặp quy đổi trong Market Watch.")
        return value

    def bars(self, symbol, timeframe, count):
        # Position 0 is the forming candle; position 1 starts closed candles.
        rates = self.mt5.copy_rates_from_pos(symbol, getattr(self.mt5, f'TIMEFRAME_{timeframe}'), 1, count)
        if rates is None:
            raise BrokerUnavailable('Không lấy được nến từ MT5. Mở chart/timeframe tương ứng để tải lịch sử.')
        bars = [{'time':int(r['time']),'high':float(r['high']),'low':float(r['low'])} for r in rates]
        if any(not all(math.isfinite(r[k]) and r[k] > 0 for k in ('high','low')) or r['low'] > r['high'] for r in bars):
            raise BrokerUnavailable('Dữ liệu nến MT5 không hợp lệ.')
        return bars

    def verify_account(self, account):
        current = self.connect()
        if str(current.login) != account["login"] or current.server != account["server"] or current.currency != account["currency"]:
            raise BrokerUnavailable("Tài khoản thay đổi trong lúc tính. Hãy tải lại.")
