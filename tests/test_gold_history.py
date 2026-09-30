from datetime import datetime, timezone
import math

import pytest

from app.broker import OnlineBroker, BrokerUnavailable
from app.gold_history import decode_candles, aggregate_closed, gold_bars
from app.stops import FRAME_SECONDS, SwingRequest, find_swing

NOW = int(datetime(2026, 9, 30, 12, 30, 30, tzinfo=timezone.utc).timestamp())


def encode(bars, seconds):
    first = bars[0]['time'] if bars else NOW // seconds * seconds
    data = {'timestamp': first * 1000, 'multiplier': .01, 'shift': seconds * 1000,
            'open': 3000, 'high': 3000, 'low': 3000, 'close': 3000,
            'times': [], 'opens': [], 'highs': [], 'lows': [], 'closes': [], 'volumes': []}
    previous = [300000] * 4
    stamp = first
    for bar in bars:
        data['times'].append((bar['time'] - stamp) // seconds)
        stamp = bar['time']
        current = [round(((bar['high'] + bar['low']) / 2) * 100),
                   round(bar['high'] * 100), round(bar['low'] * 100),
                   round(((bar['high'] + bar['low']) / 2) * 100)]
        for j, field in enumerate(('opens', 'highs', 'lows', 'closes')):
            data[field].append(current[j] - previous[j])
        previous = current
        data['volumes'].append(1)
    return data


class Response:
    status_code = 200
    def __init__(self, data): self.data = data
    def json(self): return self.data


class HistoryClient:
    def __init__(self): self.urls = []
    def get(self, url):
        self.urls.append(url)
        source = url.split('/candles/')[1].split('/')[0]
        seconds = {'minute': 60, 'hour': 3600, 'day': 86400}[source]
        if '?from=' in url:
            start = int(url.split('?from=')[1]) // 1000
            end = (NOW // seconds + 1) * seconds
        else:
            parts = [int(p) for p in url.split('/BID/')[1].split('/')]
            date = datetime(*parts, *([1] * (3 - len(parts))), tzinfo=timezone.utc)
            start = int(date.timestamp())
            if source == 'minute': end = start + 86400
            elif source == 'hour':
                end = int(datetime(date.year + (date.month == 12), date.month % 12 + 1, 1, tzinfo=timezone.utc).timestamp())
            else: end = int(datetime(date.year + 1, 1, 1, tzinfo=timezone.utc).timestamp())
        bars = []
        for t in range(start, end, seconds):
            cycle = t // (900 if seconds == 60 else seconds)
            center = 3000 + round(10 * math.sin(cycle * math.pi / 6), 2)
            bars.append({'time': t, 'high': center + 1, 'low': center - 1})
        return Response(encode(bars, seconds))


def test_delta_decode_preserves_gaps_and_prices():
    bars = [{'time': 600, 'high': 3001.23, 'low': 2998.77},
            {'time': 780, 'high': 3005.45, 'low': 3000.12}]
    assert decode_candles(encode(bars, 60), 60) == bars


def test_aggregation_excludes_forming_and_incomplete_candles():
    bars = [{'time': t, 'high': 3000 + t / 60, 'low': 2900 - t / 60} for t in range(0, 900, 60)]
    result = aggregate_closed(bars, 60, 300, 650)
    assert result == [{'time': 0, 'high': 3004, 'low': 2896}, {'time': 300, 'high': 3009, 'low': 2891}]
    assert aggregate_closed(bars[1:], 60, 300, 650) == result[1:]


@pytest.mark.parametrize('frame', FRAME_SECONDS)
def test_all_gold_timeframes_closed_utc_and_cached(frame, monkeypatch):
    monkeypatch.setattr('app.gold_history.time.time', lambda: NOW)
    client, cache = HistoryClient(), {}
    bars = gold_bars(client, cache, frame, 300)
    assert len(bars) == 300
    seconds = FRAME_SECONDS[frame]
    assert all(b['time'] % seconds == 0 and b['time'] + seconds <= NOW for b in bars)
    assert all(a['time'] < b['time'] for a, b in zip(bars, bars[1:]))
    calls = len(client.urls)
    assert gold_bars(client, cache, frame, 300) == bars
    assert len(client.urls) == calls


@pytest.mark.parametrize('mutation', [
    {'shift': 1000}, {'multiplier': 0}, {'highs': []}, {'times': [-1]},
    {'lows': [100000]}, {'timestamp': float('nan')},
])
def test_bad_history_rejected(mutation):
    data = encode([{'time': 600, 'high': 3001, 'low': 2999}], 60)
    data.update(mutation)
    with pytest.raises(BrokerUnavailable): decode_candles(data, 60)


@pytest.mark.parametrize('side', ['buy', 'sell'])
def test_gold_swing_end_to_end(side, monkeypatch):
    monkeypatch.setattr('app.gold_history.time.time', lambda: NOW)
    monkeypatch.setenv('ACCOUNT_CURRENCY', 'USD')
    broker = OnlineBroker()
    monkeypatch.setattr(broker, '_client', lambda: HistoryClient())
    monkeypatch.setattr(broker, '_fetch_quote', lambda symbol: (3000, NOW))
    result = find_swing(SwingRequest(symbol='XAUUSD', side=side, entry=3000, timeframe='M15', buffer_ticks=10), broker)
    assert result['bars_scanned'] == 300
    assert result['stop'] < 3000 if side == 'buy' else result['stop'] > 3000
    assert abs(result['stop'] - result['swing_price']) == pytest.approx(.1)
    assert result['context']['symbol']['swing_supported']


def test_source_failure_is_not_fake_swing(monkeypatch):
    class FailedClient:
        def get(self, url): raise TimeoutError()
    with pytest.raises(BrokerUnavailable, match='Không kết nối'):
        gold_bars(FailedClient(), {}, 'M15', 300)


def test_stale_history_rejected(monkeypatch):
    monkeypatch.setattr('app.gold_history.time.time', lambda: NOW)
    class StaleClient:
        def get(self, url):
            if '?from=' in url: return Response(encode([], 60))
            return HistoryClient().get(url)
    with pytest.raises(BrokerUnavailable, match='quá cũ'):
        gold_bars(StaleClient(), {}, 'M1', 300)
