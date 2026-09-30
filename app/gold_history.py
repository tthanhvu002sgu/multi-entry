"""Dukascopy XAU/USD BID candles, UTC aggregation, without synthetic gap bars.

Wire format reference: dukascopy-node src/data-normaliser/index.ts and
src/url-generator/index.ts (https://github.com/Leo4815162342/dukascopy-node).
"""
import math
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from .broker import BrokerUnavailable
from .stops import FRAME_SECONDS


def decode_candles(data, base_seconds):
    try:
        times = data['times']
        columns = [data[k] for k in ('opens', 'highs', 'lows', 'closes', 'volumes')]
        if not isinstance(times, list) or any(not isinstance(c, list) or len(c) != len(times) for c in columns):
            raise ValueError('Số phần tử OHLC không khớp')
        stamp = data['timestamp']
        multiplier = Decimal(str(data['multiplier']))
        if not math.isfinite(stamp) or stamp < 0 or not multiplier.is_finite() or multiplier <= 0:
            raise ValueError('Timestamp/multiplier không hợp lệ')
        if data['shift'] != base_seconds * 1000:
            raise ValueError('Khoảng thời gian nến không đúng')
        if not times:
            return []
        units = [(Decimal(str(data[k])) / multiplier).to_integral_value(rounding=ROUND_HALF_UP)
                 for k in ('open', 'high', 'low', 'close')]
        if any(not u.is_finite() for u in units):
            raise ValueError('Giá gốc không hợp lệ')
        bars = []
        for i, delta in enumerate(times):
            if isinstance(delta, bool) or not isinstance(delta, int) or delta < 0 or (i > 0 and delta == 0):
                raise ValueError('Thứ tự thời gian không hợp lệ')
            stamp += delta * data['shift']
            for j in range(4):
                value = columns[j][i]
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError('Giá OHLC không hợp lệ')
                units[j] += Decimal(str(value))
            o, h, l, c = [float(u * multiplier) for u in units]
            volume = columns[4][i]
            if not all(math.isfinite(p) and p > 0 for p in (o, h, l, c)) or not l <= min(o, c) <= max(o, c) <= h:
                raise ValueError('Biên OHLC không hợp lệ')
            if not isinstance(volume, (int, float)) or not math.isfinite(volume) or volume < 0:
                raise ValueError('Volume không hợp lệ')
            if stamp % (base_seconds * 1000) != 0:
                raise ValueError('Timestamp lệch khung UTC')
            # Do not manufacture candles during market closures/no trading.
            if volume > 0:
                bars.append({'time': int(stamp // 1000), 'high': h, 'low': l})
        return bars
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise BrokerUnavailable(f'Dữ liệu nến XAUUSD Dukascopy không hợp lệ: {exc}') from exc


def aggregate_closed(bars, base_seconds, seconds, now):
    groups = {}
    for bar in sorted(bars, key=lambda b: b['time']):
        start = bar['time'] // seconds * seconds
        if start + seconds > now:
            continue
        groups.setdefault(start, {})[bar['time']] = bar
    result = []
    for start, group in sorted(groups.items()):
        # A partial first bucket or a gap must not masquerade as a full candle.
        if set(group) != set(range(start, start + seconds, base_seconds)):
            continue
        result.append({'time': start, 'high': max(b['high'] for b in group.values()),
                       'low': min(b['low'] for b in group.values())})
    return result


def gold_bars(client, cache, timeframe, count):
    if timeframe not in FRAME_SECONDS or not 1 <= count <= 300:
        raise ValueError('Khung thời gian hoặc số nến XAUUSD không hợp lệ.')
    now = time.time()
    date = datetime.fromtimestamp(now, timezone.utc)
    seconds = FRAME_SECONDS[timeframe]
    if seconds < 3600:
        source, base_seconds, limit = 'minute', 60, 14
        bucket = date.replace(hour=0, minute=0, second=0, microsecond=0)
    elif seconds < 86400:
        source, base_seconds, limit = 'hour', 3600, 4
        bucket = date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        source, base_seconds, limit = 'day', 86400, 3
        bucket = date.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    collected = []
    result = []
    for index in range(limit):
        root = f'https://jetta.dukascopy.com/v1/candles/{source}/XAU-USD/BID'
        if index == 0:
            url = f'{root}?from={int(bucket.timestamp()) * 1000}'
        else:
            suffix = str(bucket.year)
            if source in ('minute', 'hour'):
                suffix += f'/{bucket.month}'
            if source == 'minute':
                suffix += f'/{bucket.day}'
            url = f'{root}/{suffix}'
        cached = cache.get(url)
        if cached and (index > 0 or now - cached[0] < 20):
            bars = cached[1]
        else:
            try:
                response = client.get(url)
                if response.status_code != 200:
                    raise BrokerUnavailable(f'Dukascopy trả mã {response.status_code} khi tải nến XAUUSD.')
                bars = decode_candles(response.json(), base_seconds)
                if any(b['time'] < bucket.timestamp() for b in bars):
                    raise BrokerUnavailable('Dukascopy trả nến ngoài khoảng yêu cầu.')
                cache[url] = (now, bars)
                if len(cache) > 32:
                    del cache[next(iter(cache))]
            except BrokerUnavailable:
                raise
            except Exception as exc:
                raise BrokerUnavailable('Không kết nối được nguồn nến XAUUSD Dukascopy. Thử lại hoặc nhập SL tay.') from exc
        collected.extend(bars)
        result = aggregate_closed(collected, base_seconds, seconds, now)
        if len(result) >= count:
            break
        if source == 'minute':
            bucket -= timedelta(days=1)
        elif source == 'hour':
            bucket = (bucket - timedelta(days=1)).replace(day=1)
        else:
            bucket = bucket.replace(year=bucket.year - 1)
    if len(result) < 5:
        raise BrokerUnavailable('Chưa đủ nến XAUUSD đã đóng để xác nhận swing. Thử lại hoặc nhập SL tay.')
    if now - (result[-1]['time'] + seconds) > max(900, seconds * 3):
        raise BrokerUnavailable('Nến XAUUSD Dukascopy quá cũ. Không dùng để lấy SL mới.')
    return result[-count:]
