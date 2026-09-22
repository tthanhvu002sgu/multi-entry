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
SECRET = secrets.token_bytes(32)
if MODE == "demo":
    broker = DemoBroker()
elif MODE == "mt5":
    broker = MT5Broker()
else:
    broker = OnlineBroker()
app = FastAPI(title="Multi Entry", docs_url=None, redoc_url=None, openapi_url=None)
attempts = defaultdict(list)
auth_lock = Lock()
DATA = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
DATA.mkdir(parents=True, exist_ok=True)


@contextmanager
def db():
    connection = sqlite3.connect(DATA / "plans.sqlite3", timeout=10)
    try:
        with connection:
            connection.execute("CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, name TEXT NOT NULL, payload TEXT NOT NULL, updated REAL NOT NULL)")
            yield connection
    finally:
        connection.close()


def valid_cookie(token):
    try:
        expires, signature = token.split(".")
        expected = hmac.new(SECRET, expires.encode(), hashlib.sha256).hexdigest()
        return int(expires) > time.time() and hmac.compare_digest(expected, signature)
    except (ValueError, AttributeError):
        return False


def require_auth(request: Request):
    if not PASSWORD:
        # Even if someone bypasses run.py with a custom uvicorn command, a
        # passwordless instance never serves remote clients or proxied traffic.
        if request.client.host not in ("127.0.0.1", "::1", "testclient") or request.headers.get("x-forwarded-for"):
            raise HTTPException(403, "Cần cấu hình APP_PASSWORD để dùng từ xa.")
    elif not valid_cookie(request.cookies.get("session")):
        raise HTTPException(401, "Vui lòng đăng nhập.")


@app.middleware("http")
async def headers(request, call_next):
    # Reject cross-origin mutations even in unauthenticated localhost demo.
    if request.method in ("POST", "DELETE", "PUT"):
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            return JSONResponse({"detail": "Origin không hợp lệ."}, status_code=403)
    response = await call_next(request)
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
    return {"required": bool(PASSWORD), "authenticated": not PASSWORD or valid_cookie(request.cookies.get("session")), "mode": MODE}


class Login(BaseModel):
    password: str = Field(max_length=256)


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
    expires = str(int(time.time() + 12 * 3600))
    signature = hmac.new(SECRET, expires.encode(), hashlib.sha256).hexdigest()
    response.set_cookie("session", f"{expires}.{signature}", httponly=True, samesite="strict", secure=os.getenv("COOKIE_SECURE", "false").lower() == "true", max_age=12 * 3600)
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("session")
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
