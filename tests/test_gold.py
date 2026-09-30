from datetime import datetime, timezone
import importlib
import time

import pytest
from fastapi.testclient import TestClient

from app.broker import OnlineBroker, BrokerUnavailable
from app.engine import Plan, calculate
from tests.test_online_broker import MockClient


@pytest.fixture
def gold(monkeypatch):
    monkeypatch.setenv('ACCOUNT_CURRENCY', 'USD')
    monkeypatch.setenv('MAX_TICK_AGE_SECONDS', '120')
    for key in ('CONTRACT_SIZE', 'TICK_SIZE', 'VOLUME_MIN', 'VOLUME_STEP', 'VOLUME_MAX'):
        monkeypatch.delenv('XAUUSD_' + key, raising=False)
    broker = OnlineBroker()
    data = {'symbol': 'XAU', 'currency': 'USD', 'price': 3000,
            'updatedAt': datetime.now(timezone.utc).isoformat()}
    monkeypatch.setattr(broker, '_client', lambda: MockClient(data))
    return broker, data


@pytest.mark.parametrize('side,stop', [('buy', 2990), ('sell', 3010)])
def test_gold_risk_and_contract(gold, side, stop):
    broker, _ = gold
    result = calculate(Plan(symbol='XAUUSD', side=side, entry=3000, stop=stop,
                            count=4, budget=60), broker)
    assert result['lot_each'] == .02
    assert result['total_loss'] == 50
    assert result['context']['symbol']['contract_size'] == 100
    assert result['context']['symbol']['tick_size'] == .01
    assert not result['context']['symbol']['swing_supported']
    assert result['context']['account']['server'] == 'Gold API (Spot Gold)'
    assert broker.profit('XAUUSD', side, .01, 3000, stop) == -10


def test_gold_custom_contract(gold, monkeypatch):
    broker, _ = gold
    monkeypatch.setenv('XAUUSD_CONTRACT_SIZE', '10')
    monkeypatch.setenv('XAUUSD_TICK_SIZE', '0.001')
    assert broker.profit('XAUUSD', 'buy', .01, 3000, 2990) == -1
    assert broker.context('XAUUSD')['symbol']['digits'] == 3


@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf', 'oops'])
def test_gold_invalid_contract(gold, monkeypatch, value):
    broker, _ = gold
    monkeypatch.setenv('XAUUSD_CONTRACT_SIZE', value)
    with pytest.raises(ValueError):
        broker.context('XAUUSD')


@pytest.mark.parametrize('mutation', [
    {'price': 0}, {'price': float('nan')}, {'currency': 'EUR'},
    {'symbol': 'XAG'}, {'updatedAt': 'invalid'},
    {'updatedAt': datetime.fromtimestamp(time.time() - 1000, timezone.utc).isoformat()},
])
def test_gold_invalid_feed(gold, mutation):
    broker, data = gold
    data.update(mutation)
    with pytest.raises(BrokerUnavailable):
        broker.context('XAUUSD')


def test_gold_non_usd_and_swing_rejected(gold, monkeypatch):
    broker, _ = gold
    with pytest.raises(ValueError, match='SL thủ công'):
        broker.bars('XAUUSD', 'M15', 300)
    monkeypatch.setenv('ACCOUNT_CURRENCY', 'EUR')
    with pytest.raises(ValueError, match='ACCOUNT_CURRENCY=USD'):
        broker.context('XAUUSD')


def test_gold_watchlist_and_calculation_api(tmp_path, monkeypatch, gold):
    broker, _ = gold
    monkeypatch.setenv('APP_MODE', 'online')
    monkeypatch.setenv('APP_PASSWORD', 'test-password-123')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    from app import main
    importlib.reload(main)
    monkeypatch.setattr(main, 'broker', broker)
    with TestClient(main.app) as client:
        client.post('/api/login', json={'password': 'test-password-123'})
        response = client.post('/api/symbols', json={'symbol': 'xauusd'})
        assert response.status_code == 200
        assert 'XAUUSD' in response.json()['symbols']
        response = client.post('/api/calculate', json={
            'symbol': 'XAUUSD', 'entry': 3000, 'stop': 2990, 'count': 4, 'budget': 60})
        assert response.status_code == 200
        assert response.json()['total_loss'] == 50
