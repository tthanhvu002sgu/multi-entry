import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .broker import BrokerUnavailable, DemoBroker, MT5Broker, OnlineBroker
from .engine import Plan, calculate
from .stops import SwingRequest, StepRequest, find_swing, step_stop

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
MODE = os.getenv("APP_MODE", "demo")
if MODE not in ("demo", "mt5", "online"):
    raise RuntimeError("APP_MODE must be demo, mt5, or online")
PASSWORD = os.getenv("APP_PASSWORD", "")
if (MODE == "mt5" or os.getenv("HOST", "127.0.0.1") not in ("127.0.0.1", "localhost", "::1")) and len(PASSWORD) < 12:
    raise RuntimeError("Set APP_PASSWORD with at least 12 characters before MT5 or remote access.")
DATA = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
DATA.mkdir(parents=True, exist_ok=True)


def get_or_create_secret(data_dir: Path) -> bytes:
    env_secret = os.getenv("SESSION_SECRET")
    if env_secret:
        return hashlib.sha256(env_secret.encode()).digest()
    secret_file = data_dir / ".session_secret"
    if secret_file.exists():
        try:
            val = secret_file.read_text().strip()
            if len(val) == 64:
                return bytes.fromhex(val)
        except Exception:
            pass
    secret = secrets.token_bytes(32)
    try:
        secret_file.write_text(secret.hex())
    except Exception:
        pass
    return secret


SECRET = get_or_create_secret(DATA)
if MODE == "demo":
    broker = DemoBroker()
elif MODE == "mt5":
    broker = MT5Broker()
else:
    broker = OnlineBroker()
app = FastAPI(title="Multi Entry", docs_url=None, redoc_url=None, openapi_url=None)
attempts = defaultdict(list)
auth_lock = Lock()

SESSION_COOKIE = "session"
DEVICE_COOKIE = "remember_device"
SESSION_MAX_AGE = 12 * 3600  # 12 hours
DEVICE_MAX_AGE = 30 * 86400  # 30 days


def is_cookie_secure() -> bool:
    return os.getenv("COOKIE_SECURE", "false").lower() == "true"


@contextmanager
def db():
    connection = sqlite3.connect(DATA / "plans.sqlite3", timeout=10)
    try:
        with connection:
            connection.execute("CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, name TEXT NOT NULL, payload TEXT NOT NULL, updated REAL NOT NULL)")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS trusted_devices ("
                "id TEXT PRIMARY KEY, "
                "series TEXT UNIQUE NOT NULL, "
                "token_hash TEXT NOT NULL, "
                "user_agent TEXT, "
                "created_at REAL NOT NULL, "
                "last_used REAL NOT NULL, "
                "expires_at REAL NOT NULL"
                ")"
            )
            yield connection
    finally:
        connection.close()


def make_session_token() -> str:
    expires = str(int(time.time() + SESSION_MAX_AGE))
    signature = hmac.new(SECRET, expires.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def valid_cookie(token: str | None) -> bool:
    if not token:
        return False
    try:
        expires, signature = token.split(".")
        expected = hmac.new(SECRET, expires.encode(), hashlib.sha256).hexdigest()
        return int(expires) > time.time() and hmac.compare_digest(expected, signature)
    except (ValueError, AttributeError):
        return False


def issue_trusted_device(conn: sqlite3.Connection, user_agent: str) -> tuple[str, str]:
    dev_id = secrets.token_hex(12)
    series = secrets.token_hex(16)
    raw_token = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    now = time.time()
    expires_at = now + DEVICE_MAX_AGE
    conn.execute(
        "INSERT INTO trusted_devices VALUES (?,?,?,?,?,?,?)",
        (dev_id, series, token_hash, user_agent, now, now, expires_at),
    )
    return series, raw_token


def verify_and_rotate_device(
    conn: sqlite3.Connection, cookie_val: str | None, user_agent: str
) -> tuple[bool, str | None]:
    if not cookie_val or "." not in cookie_val:
        return False, None
    try:
        series, raw_token = cookie_val.split(".", 1)
    except ValueError:
        return False, None

    row = conn.execute(
        "SELECT id, token_hash, expires_at FROM trusted_devices WHERE series=?",
        (series,),
    ).fetchone()
    if not row:
        return False, None

    dev_id, stored_token_hash, expires_at = row
    now = time.time()
    if expires_at < now:
        conn.execute("DELETE FROM trusted_devices WHERE id=?", (dev_id,))
        return False, None

    given_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    if not hmac.compare_digest(stored_token_hash, given_hash):
        # THEFT DETECTION: token already rotated or reused -> revoke entire series
        conn.execute("DELETE FROM trusted_devices WHERE series=?", (series,))
        return False, None

    # Rotate token
    new_raw_token = secrets.token_hex(32)
    new_token_hash = hashlib.sha256(new_raw_token.encode()).hexdigest()
    new_expires_at = now + DEVICE_MAX_AGE
    conn.execute(
        "UPDATE trusted_devices SET token_hash=?, last_used=?, expires_at=?, user_agent=? WHERE id=?",
        (new_token_hash, now, new_expires_at, user_agent, dev_id),
    )
    return True, f"{series}.{new_raw_token}"


def revoke_trusted_device(conn: sqlite3.Connection, cookie_val: str | None):
    if not cookie_val or "." not in cookie_val:
        return
    try:
        series, _ = cookie_val.split(".", 1)
        conn.execute("DELETE FROM trusted_devices WHERE series=?", (series,))
    except Exception:
        pass


def require_auth(request: Request):
    if not PASSWORD:
        # Even if someone bypasses run.py with a custom uvicorn command, a
        # passwordless instance never serves remote clients or proxied traffic.
        if request.client.host not in ("127.0.0.1", "::1", "testclient") or request.headers.get("x-forwarded-for"):
            raise HTTPException(403, "Cần cấu hình APP_PASSWORD để dùng từ xa.")
    elif not getattr(request.state, "authenticated", False):
        raise HTTPException(401, "Vui lòng đăng nhập.")


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Reject cross-origin mutations even in unauthenticated localhost demo.
    if request.method in ("POST", "DELETE", "PUT"):
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            return JSONResponse({"detail": "Origin không hợp lệ."}, status_code=403)

    # Resolve authentication
    sess_cookie = request.cookies.get(SESSION_COOKIE)
    if valid_cookie(sess_cookie):
        request.state.authenticated = True
    elif PASSWORD and request.url.path not in ("/api/login", "/api/logout"):
        dev_cookie = request.cookies.get(DEVICE_COOKIE)
        if dev_cookie:
            with db() as conn:
                user_agent = request.headers.get("user-agent", "")
                valid, new_dev_cookie = verify_and_rotate_device(conn, dev_cookie, user_agent)
                if valid and new_dev_cookie:
                    request.state.authenticated = True
                    request.state.new_session_cookie = make_session_token()
                    request.state.new_device_cookie = new_dev_cookie
                else:
                    request.state.authenticated = False
                    request.state.clear_device_cookie = True
        else:
            request.state.authenticated = False
    elif not PASSWORD:
        request.state.authenticated = True
    else:
        request.state.authenticated = False

    response = await call_next(request)

    if hasattr(request.state, "new_session_cookie"):
        response.set_cookie(
            SESSION_COOKIE,
            request.state.new_session_cookie,
            httponly=True,
            samesite="strict",
            secure=is_cookie_secure(),
            max_age=SESSION_MAX_AGE,
        )
        response.set_cookie(
            DEVICE_COOKIE,
            request.state.new_device_cookie,
            httponly=True,
            samesite="strict",
            secure=is_cookie_secure(),
            max_age=DEVICE_MAX_AGE,
        )
    elif getattr(request.state, "clear_device_cookie", False):
        response.delete_cookie(DEVICE_COOKIE)

    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    return response


@app.exception_handler(BrokerUnavailable)
async def unavailable(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.exception_handler(ValueError)
async def bad_input(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/api/session")
def session(request: Request):
    is_authed = getattr(request.state, "authenticated", False) if PASSWORD else True
    return {"required": bool(PASSWORD), "authenticated": is_authed, "mode": MODE}


class Login(BaseModel):
    password: str = Field(max_length=256)
    remember: bool = Field(default=False)


@app.post("/api/login")
def login(body: Login, request: Request, response: Response):
    key = request.client.host
    with auth_lock:
        now = time.time()
        for ip in list(attempts):
            attempts[ip] = [t for t in attempts[ip] if t > now - 300]
            if not attempts[ip]:
                del attempts[ip]
        if len(attempts[key]) >= 10:
            raise HTTPException(429, "Thử lại sau 5 phút.")
        if not PASSWORD or not hmac.compare_digest(body.password.encode(), PASSWORD.encode()):
            attempts[key].append(now)
            raise HTTPException(401, "Mật khẩu không đúng.")
        attempts.pop(key, None)

    response.set_cookie(
        SESSION_COOKIE,
        make_session_token(),
        httponly=True,
        samesite="strict",
        secure=is_cookie_secure(),
        max_age=SESSION_MAX_AGE,
    )
    if body.remember:
        with db() as conn:
            user_agent = request.headers.get("user-agent", "")
            series, raw_token = issue_trusted_device(conn, user_agent)
            response.set_cookie(
                DEVICE_COOKIE,
                f"{series}.{raw_token}",
                httponly=True,
                samesite="strict",
                secure=is_cookie_secure(),
                max_age=DEVICE_MAX_AGE,
            )
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    dev_cookie = request.cookies.get(DEVICE_COOKIE)
    if dev_cookie:
        with db() as conn:
            revoke_trusted_device(conn, dev_cookie)
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(DEVICE_COOKIE)
    return {"ok": True}


@app.get("/api/symbols", dependencies=[Depends(require_auth)])
def symbols():
    with broker.lock:
        return {"symbols": broker.symbols()}


@app.get("/api/context", dependencies=[Depends(require_auth)])
def context(symbol: str):
    with broker.lock:
        return broker.context(symbol)


@app.post("/api/calculate", dependencies=[Depends(require_auth)])
def compute(plan: Plan):
    with broker.lock:
        return calculate(plan, broker)


class SavedPlan(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    plan: Plan
    currency: str = Field(min_length=1, max_length=20)


@app.post('/api/swing', dependencies=[Depends(require_auth)])
def swing(body: SwingRequest):
    with broker.lock:
        return find_swing(body, broker)


@app.post('/api/stop-step', dependencies=[Depends(require_auth)])
def adjust_stop(body: StepRequest):
    with broker.lock:
        return step_stop(body, broker)


@app.get("/api/plans", dependencies=[Depends(require_auth)])
def plans():
    with db() as conn:
        rows = conn.execute("SELECT id,name,payload,updated FROM plans ORDER BY updated DESC LIMIT 100").fetchall()
    return [{"id": r[0], "name": r[1], **json.loads(r[2]), "updated": r[3]} for r in rows]


@app.post("/api/plans", dependencies=[Depends(require_auth)])
def save(body: SavedPlan):
    identifier = secrets.token_hex(12)
    with db() as conn:
        payload = json.dumps({"plan": body.plan.model_dump(), "currency": body.currency})
        conn.execute("INSERT INTO plans VALUES (?,?,?,?)", (identifier, body.name, payload, time.time()))
        conn.execute("DELETE FROM plans WHERE id NOT IN (SELECT id FROM plans ORDER BY updated DESC LIMIT 100)")
    return {"id": identifier}


@app.delete("/api/plans/{identifier}", dependencies=[Depends(require_auth)])
def delete(identifier: str):
    with db() as conn:
        conn.execute("DELETE FROM plans WHERE id=?", (identifier,))
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
