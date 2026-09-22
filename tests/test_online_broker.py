import math
import pytest
from app.broker import OnlineBroker, BrokerUnavailable
from app.stops import find_swing, SwingRequest
from app.engine import Plan, calculate


def test_online_broker_symbols():
    b = OnlineBroker()
    symbols = b.symbols()
    assert "EURUSD" in symbols
    assert "USDJPY" in symbols
    assert "GBPUSD" in symbols
    assert len(symbols) >= 20


def test_online_broker_profit_direct():
    b = OnlineBroker()
    # EURUSD: Quote is USD
    buy_loss = b.profit("EURUSD", "buy", 1.0, 1.1000, 1.0900)
    assert pytest.approx(buy_loss) == -1000.0

    sell_loss = b.profit("EURUSD", "sell", 1.0, 1.1000, 1.1100)
    assert pytest.approx(sell_loss) == -1000.0


def test_online_broker_profit_indirect():
    b = OnlineBroker()
    # USDJPY: Base is USD, Quote is JPY
    # (149.0 - 150.0) * 100000 * 1.0 / 149.0
    expected = -100000.0 / 149.0
    loss = b.profit("USDJPY", "buy", 1.0, 150.0, 149.0)
    assert pytest.approx(loss) == expected


def test_online_broker_profit_cross(monkeypatch):
    b = OnlineBroker()
    # EURGBP: Quote is GBP. Needs GBPUSD rate.
    monkeypatch.setattr(b, "_get_rate", lambda pair: 1.25 if pair == "GBPUSD" else 1.0)
    loss = b.profit("EURGBP", "buy", 1.0, 0.8500, 0.8400)
    # (-0.01) * 100000 * 1.0 * 1.25 = -1250 USD
    assert pytest.approx(loss) == -1250.0

    # EURJPY: Quote is JPY. Needs USDJPY rate.
    monkeypatch.setattr(b, "_get_rate", lambda pair: 150.0 if pair == "USDJPY" else 1.0)
    loss_jpy = b.profit("EURJPY", "buy", 1.0, 160.0, 159.0)
    # (-1.0) * 100000 * 1.0 / 150.0 = -666.67 USD
    assert pytest.approx(loss_jpy) == -100000.0 / 150.0


def test_online_broker_context_mocked(monkeypatch):
    b = OnlineBroker()
    monkeypatch.setattr(b, "_fetch_quote", lambda sym: (1.1050, 1700000000))
    ctx = b.context("EURUSD")
    assert ctx["mode"] == "online"
    assert ctx["symbol"]["name"] == "EURUSD"
    assert ctx["symbol"]["tick_size"] == 0.00001
    assert ctx["symbol"]["digits"] == 5
    assert ctx["account"]["currency"] == "USD"
    assert ctx["account"]["equity"] >= 1000


class MockResp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def json(self):
        return self._data


class MockClient:
    def __init__(self, data):
        self._data = data

    def get(self, url):
        return MockResp(self._data)


def test_online_broker_bars_mocked(monkeypatch):
    b = OnlineBroker()

    fake_response = {
        "chart": {
            "result": [
                {
                    "timestamp": [100, 200, 300, 400, 500, 600, 700],
                    "indicators": {
                        "quote": [
                            {
                                "high": [1.11, 1.12, 1.13, 1.14, 1.15, 1.16, 1.17],
                                "low":  [1.09, 1.10, 1.11, 1.12, 1.13, 1.14, 1.15],
                            }
                        ]
                    }
                }
            ]
        }
    }

    monkeypatch.setattr(b, "_client", lambda: MockClient(fake_response))

    bars = b.bars("EURUSD", "M15", 300)
    # Last candle (index 6, timestamp 700) is the forming bar and must be dropped
    assert len(bars) == 6
    assert bars[-1]["time"] == 600
    assert bars[-1]["high"] == 1.16
    assert bars[-1]["low"] == 1.14


def test_online_broker_find_swing(monkeypatch):
    b = OnlineBroker()
    # 10 bars with clear swing low at index 6
    lows = [1.095, 1.094, 1.090, 1.094, 1.095, 1.093, 1.091, 1.093, 1.094, 1.088, 1.090]
    fake_response = {
        "chart": {
            "result": [
                {
                    "timestamp": [100 + i * 60 for i in range(len(lows))],
                    "indicators": {
                        "quote": [
                            {
                                "high": [l + 0.005 for l in lows],
                                "low":  lows,
                            }
                        ]
                    }
                }
            ]
        }
    }

    monkeypatch.setattr(b, "_client", lambda: MockClient(fake_response))
    monkeypatch.setattr(b, "_fetch_quote", lambda sym: (1.1000, 1700000000))

    req = SwingRequest(symbol="EURUSD", side="buy", entry=1.1000, timeframe="M15")
    res = find_swing(req, b)
    assert res["kind"] == "low"
    # lows[:-1] has index 6 as confirmed low (1.091)
    assert res["stop"] == 1.091


def test_online_broker_calculate(monkeypatch):
    b = OnlineBroker()
    monkeypatch.setattr(b, "_fetch_quote", lambda sym: (1.1000, 1700000000))
    plan = Plan(symbol="EURUSD", side="buy", entry=1.1000, stop=1.0900, count=4, budget=60, sizing="budget")
    result = calculate(plan, b)
    assert result["total_loss"] == 50.0
    assert result["lot_each"] == 0.02
    assert result["context"]["mode"] == "online"
    assert len(result["entries"]) == 4
