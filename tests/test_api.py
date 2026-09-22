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


def test_remember_device_and_rotation(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_MODE', 'demo')
    monkeypatch.setenv('APP_PASSWORD', 'test-password-123')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    from app import main
    importlib.reload(main)
    client = TestClient(main.app)

    # Login with remember=True
    resp = client.post('/api/login', json={'password': 'test-password-123', 'remember': True})
    assert resp.status_code == 200
    assert 'remember_device' in client.cookies
    initial_device_cookie = client.cookies['remember_device']
    assert 'session' in client.cookies

    # Delete session cookie to simulate 12h expiration / closed session
    del client.cookies['session']
    assert 'session' not in client.cookies

    # Request /api/session -> middleware recognizes remember_device, issues new session cookie and rotates remember_device
    sess_resp = client.get('/api/session')
    assert sess_resp.status_code == 200
    assert sess_resp.json()['authenticated'] is True
    assert 'session' in client.cookies
    rotated_device_cookie = client.cookies['remember_device']
    assert rotated_device_cookie != initial_device_cookie

    # Subsequent protected calls succeed
    assert client.get('/api/symbols').status_code == 200


def test_token_reuse_theft_detection(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_MODE', 'demo')
    monkeypatch.setenv('APP_PASSWORD', 'test-password-123')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    from app import main
    importlib.reload(main)
    client = TestClient(main.app)

    # Login and capture initial device token
    client.post('/api/login', json={'password': 'test-password-123', 'remember': True})
    stolen_token = client.cookies['remember_device']

    # Legitimate use: delete session cookie and make request -> token rotates to new token
    del client.cookies['session']
    client.get('/api/session')
    legit_token = client.cookies['remember_device']
    assert legit_token != stolen_token

    # Attacker tries to use stolen_token
    attacker_client = TestClient(main.app)
    attacker_client.cookies.set('remember_device', stolen_token)
    att_resp = attacker_client.get('/api/session')
    assert att_resp.json()['authenticated'] is False

    # Legitimate user tries to use legit_token -> should now fail because entire series was revoked on theft detection
    legit_client = TestClient(main.app)
    legit_client.cookies.set('remember_device', legit_token)
    legit_resp = legit_client.get('/api/session')
    assert legit_resp.json()['authenticated'] is False


def test_logout_revokes_trusted_device(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_MODE', 'demo')
    monkeypatch.setenv('APP_PASSWORD', 'test-password-123')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    from app import main
    importlib.reload(main)
    client = TestClient(main.app)

    # Login with remember=True
    client.post('/api/login', json={'password': 'test-password-123', 'remember': True})
    saved_device_cookie = client.cookies['remember_device']

    # Logout
    logout_resp = client.post('/api/logout')
    assert logout_resp.status_code == 200
    assert 'remember_device' not in client.cookies or client.cookies['remember_device'] == ''

    # Trying to reuse old remember_device should fail
    retest_client = TestClient(main.app)
    retest_client.cookies.set('remember_device', saved_device_cookie)
    assert retest_client.get('/api/session').json()['authenticated'] is False


def test_persistent_secret_across_server_restarts(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_MODE', 'demo')
    monkeypatch.setenv('APP_PASSWORD', 'test-password-123')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    from app import main
    importlib.reload(main)
    client = TestClient(main.app)

    # Login without remember (session cookie only)
    client.post('/api/login', json={'password': 'test-password-123', 'remember': False})
    session_cookie = client.cookies['session']

    # Restart server with same DATA_DIR
    importlib.reload(main)
    restarted_client = TestClient(main.app)
    restarted_client.cookies.set('session', session_cookie)

    # Session cookie must still be valid
    assert restarted_client.get('/api/session').json()['authenticated'] is True
    assert restarted_client.get('/api/symbols').status_code == 200

