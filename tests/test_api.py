import importlib
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def server(tmp_path,monkeypatch):
    monkeypatch.setenv('APP_MODE','demo')
    monkeypatch.setenv('APP_PASSWORD','test-password-123')
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    from app import main
    importlib.reload(main)
    return TestClient(main.app)


def test_auth_and_plan_lifecycle(server):
    assert server.get('/api/symbols').status_code == 401
    assert server.post('/api/login',json={'password':'wrong'}).status_code == 401
    response=server.post('/api/login',json={'password':'test-password-123'})
    assert response.status_code == 200
    assert 'HttpOnly' in response.headers['set-cookie']
    plan={'symbol':'EURUSD','entry':1.1,'stop':1.09,'count':4,'budget':60}
    result=server.post('/api/calculate',json=plan)
    assert result.status_code == 200
    assert result.json()['lot_each'] == .02
    saved=server.post('/api/plans',json={'name':'My plan','plan':plan,'currency':'USD'}).json()
    assert len(server.get('/api/plans').json()) == 1
    server.delete('/api/plans/'+saved['id'])
    assert server.get('/api/plans').json() == []
    server.post('/api/logout')
    assert server.get('/api/plans').status_code == 401


def test_cross_origin_and_invalid_inputs(server):
    assert server.post('/api/login',json={'password':'test-password-123'},headers={'Origin':'https://evil.example'}).status_code == 403
    server.post('/api/login',json={'password':'test-password-123'})
    assert server.post('/api/calculate',json={'symbol':'EURUSD','entry':1.1,'stop':1.09,'count':0}).status_code == 422
    assert server.get('/').status_code == 200


def test_login_rate_limit(server):
    for _ in range(10): server.post('/api/login',json={'password':'wrong'})
    assert server.post('/api/login',json={'password':'wrong'}).status_code == 429


def test_swing_and_step_endpoints(server):
    body={'symbol':'USDJPY','side':'buy','entry':150,'timeframe':'M15'}
    assert server.post('/api/swing',json=body).status_code==401
    server.post('/api/login',json={'password':'test-password-123'})
    result=server.post('/api/swing',json=body)
    assert result.status_code==200
    assert result.json()['stop'] < 150
    result=server.post('/api/stop-step',json={'symbol':'USDJPY','side':'buy','entry':150,'stop':149,'direction':'up','ticks':10})
    assert result.status_code==200
    assert result.json()['stop']==149.01
    assert server.post('/api/swing',json=body|{'timeframe':'BAD'}).status_code==422


def test_online_api_mode(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_MODE', 'online')
    monkeypatch.setenv('APP_PASSWORD', 'test-password-123')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    from app import main
    importlib.reload(main)
    client = TestClient(main.app)
    assert main.broker.mode == 'online'
    sess = client.get('/api/session').json()
    assert sess['mode'] == 'online'
