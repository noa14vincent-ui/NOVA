"""NOVA Agenda account + shared-calendar REST routes.

Mount into the existing NOVA FastAPI app:
    from agenda_backend import install_agenda_routes
    install_agenda_routes(app)

Set NOVA_AGENDA_DB_PATH to a durable database file on Render, e.g.
/var/data/nova_agenda.db after attaching a persistent disk at /var/data.
PostgreSQL is used automatically when DATABASE_URL or NOVA_AGENDA_DATABASE_URL
is set (install psycopg[binary]); otherwise SQLite is used. For Render, choose a
durable Postgres database for shared account data rather than the ephemeral filesystem.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("nova.agenda")
BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = (os.getenv("NOVA_AGENDA_DATABASE_URL") or os.getenv("DATABASE_URL", "")).strip()
USE_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))
DB_PATH = Path(os.getenv("NOVA_AGENDA_DB_PATH", str(BASE_DIR / "nova_agenda.db"))).expanduser()
SESSION_COOKIE = "nova_agenda_session"
SESSION_DAYS = 14
PASSWORD_ITERATIONS = 310_000
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
AUTH_HITS: dict[str, deque[float]] = defaultdict(deque)
AUTH_HITS_LIMIT = 12


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")


class PostgresCompatConnection:
    """Small adapter so route SQL can use qmark placeholders on SQLite/Postgres."""
    def __init__(self, conn: Any):
        self._conn = conn

    def execute(self, sql: str, params: Any = ()):
        return self._conn.execute(sql.replace("?", "%s"), params)

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self._conn.execute(statement)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


def db_connect() -> Any:
    if USE_POSTGRES:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("PostgreSQL configuré mais psycopg absent. Ajoutez psycopg[binary]>=3.2,<4 à requirements.txt.") from exc
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"): ]
        return PostgresCompatConnection(psycopg.connect(url, row_factory=dict_row, connect_timeout=10))
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


@contextmanager
def db_session():
    """Connection context manager that commits/rolls back and always closes."""
    conn = db_connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db_session() as conn:
        if not USE_POSTGRES:
            conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS agenda_users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL DEFAULT '',
            password_salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agenda_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES agenda_users(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agenda_sessions_user ON agenda_sessions(user_id);
        CREATE TABLE IF NOT EXISTS agenda_events (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES agenda_users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            start_at TEXT NOT NULL,
            end_at TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT 'personal',
            color TEXT NOT NULL DEFAULT '#9b8cff',
            all_day INTEGER NOT NULL DEFAULT 0,
            timezone TEXT NOT NULL DEFAULT 'Europe/Paris',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agenda_events_user_start ON agenda_events(user_id, start_at);
        """)


def password_digest(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return salt.hex(), derived.hex()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def request_ip(request: Request) -> str:
    # Use direct peer IP rather than an untrusted X-Forwarded-For value.
    return request.client.host if request.client else "unknown"


def auth_rate_limit(request: Request) -> None:
    now = time.monotonic()
    ip = request_ip(request)
    bucket = AUTH_HITS[ip]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= AUTH_HITS_LIMIT:
        raise HTTPException(status_code=429, detail="Trop de tentatives. Réessayez dans une minute.")
    bucket.append(now)
    if len(AUTH_HITS) > 4000:
        for key in list(AUTH_HITS)[:1000]:
            if not AUTH_HITS[key]:
                AUTH_HITS.pop(key, None)


def same_origin_check(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    from urllib.parse import urlsplit
    try:
        origin_host = urlsplit(origin).netloc.lower()
        request_host = request.headers.get("host", "").lower()
        if not origin_host or origin_host != request_host:
            raise HTTPException(status_code=403, detail="Origine de requête non autorisée.")
    except ValueError:
        raise HTTPException(status_code=403, detail="Origine de requête non autorisée.")


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, request: Request) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=secure, samesite="lax")


def public_user(row: sqlite3.Row) -> dict[str, str]:
    return {"id": row["id"], "email": row["email"], "name": row["display_name"]}


def get_current_user(request: Request) -> sqlite3.Row:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or len(token) > 200:
        raise HTTPException(status_code=401, detail="Connectez-vous pour accéder à votre agenda synchronisé.")
    now = iso_now()
    with db_session() as conn:
        row = conn.execute(
            "SELECT u.id, u.email, u.display_name FROM agenda_sessions s JOIN agenda_users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?",
            (token_hash(token), now),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Session expirée. Reconnectez-vous.")
    return row


def parse_iso(value: str, label: str) -> str:
    raw = value.strip()
    if not raw or len(raw) > 64:
        raise HTTPException(status_code=422, detail=f"Champ {label} invalide.")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Date {label} invalide. Utilisez un format ISO 8601.")


class RegisterBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    name: str = Field(default="", max_length=80)


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class EventBody(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    start_at: str = Field(min_length=1, max_length=64)
    end_at: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=4000)
    location: str = Field(default="", max_length=250)
    category: str = Field(default="personal", max_length=32)
    color: str = Field(default="#9b8cff", max_length=16)
    all_day: bool = False
    timezone: str = Field(default="Europe/Paris", max_length=64)


def clean_event_payload(body: EventBody) -> dict[str, Any]:
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Le titre ne peut pas être vide.")
    start = parse_iso(body.start_at, "début")
    end = parse_iso(body.end_at, "fin")
    if end <= start:
        raise HTTPException(status_code=422, detail="La fin doit être postérieure au début.")
    category = body.category if body.category in {"personal", "work", "important", "health", "other"} else "other"
    color = body.color if re.fullmatch(r"#[0-9a-fA-F]{6}", body.color) else "#9b8cff"
    tz = body.timezone.strip() or "Europe/Paris"
    return {
        "title": title,
        "start_at": start,
        "end_at": end,
        "description": body.description.strip(),
        "location": body.location.strip(),
        "category": category,
        "color": color,
        "all_day": int(body.all_day),
        "timezone": tz,
    }


def event_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["all_day"] = bool(item["all_day"])
    return item


def install_agenda_routes(app: FastAPI) -> None:
    """Initialise DB and install same-origin account + event routes onto NOVA app."""
    init_db()

    @app.middleware("http")
    async def agenda_no_store(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/agenda/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/agenda", include_in_schema=False)
    @app.get("/agenda/", include_in_schema=False)
    def agenda_home():
        path = BASE_DIR / "agenda.html"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Le fichier agenda.html est introuvable.")
        return FileResponse(path, headers={"Cache-Control": "no-cache"})

    @app.post("/api/agenda/auth/register")
    def agenda_register(body: RegisterBody, request: Request, response: Response):
        same_origin_check(request)
        auth_rate_limit(request)
        email = body.email.strip().lower()
        if not EMAIL_RE.fullmatch(email):
            raise HTTPException(status_code=422, detail="Adresse e-mail invalide.")
        password = body.password
        if len(password) < 12 or len(password) > 128:
            raise HTTPException(status_code=422, detail="Le mot de passe doit contenir entre 12 et 128 caractères.")
        salt, digest = password_digest(password)
        user_id = str(uuid.uuid4())
        try:
            with db_session() as conn:
                conn.execute(
                    "INSERT INTO agenda_users(id,email,display_name,password_salt,password_hash,created_at) VALUES(?,?,?,?,?,?)",
                    (user_id, email, body.name.strip()[:80], salt, digest, iso_now()),
                )
                user = conn.execute("SELECT id,email,display_name FROM agenda_users WHERE id=?", (user_id,)).fetchone()
        except Exception as exc:
            if isinstance(exc, sqlite3.IntegrityError) or exc.__class__.__name__ in {"UniqueViolation", "IntegrityError"}:
                raise HTTPException(status_code=409, detail="Un compte existe déjà avec cette adresse e-mail.")
            raise
        token = secrets.token_urlsafe(40)
        with db_session() as conn:
            conn.execute("INSERT INTO agenda_sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)", (token_hash(token), user_id, (utc_now()+timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds").replace("+00:00","Z"), iso_now()))
        set_session_cookie(response, request, token)
        return {"user": public_user(user)}

    @app.post("/api/agenda/auth/login")
    def agenda_login(body: LoginBody, request: Request, response: Response):
        same_origin_check(request)
        auth_rate_limit(request)
        email = body.email.strip().lower()
        with db_session() as conn:
            row = conn.execute("SELECT * FROM agenda_users WHERE email=?", (email,)).fetchone()
        valid = False
        if row:
            _, candidate = password_digest(body.password, row["password_salt"])
            valid = hmac.compare_digest(candidate, row["password_hash"])
        if not valid:
            raise HTTPException(status_code=401, detail="Adresse e-mail ou mot de passe incorrect.")
        token = secrets.token_urlsafe(40)
        with db_session() as conn:
            conn.execute("INSERT INTO agenda_sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)", (token_hash(token), row["id"], (utc_now()+timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds").replace("+00:00","Z"), iso_now()))
            # Remove expired sessions and cap active sessions per account.
            conn.execute("DELETE FROM agenda_sessions WHERE expires_at<=?", (iso_now(),))
            excess = conn.execute("SELECT token_hash FROM agenda_sessions WHERE user_id=? ORDER BY created_at DESC LIMIT 10000 OFFSET 10", (row["id"],)).fetchall()
            for old in excess:
                conn.execute("DELETE FROM agenda_sessions WHERE token_hash=?", (old["token_hash"],))
        set_session_cookie(response, request, token)
        return {"user": public_user(row)}

    @app.post("/api/agenda/auth/logout")
    def agenda_logout(request: Request, response: Response):
        same_origin_check(request)
        token = request.cookies.get(SESSION_COOKIE, "")
        if token:
            with db_session() as conn:
                conn.execute("DELETE FROM agenda_sessions WHERE token_hash=?", (token_hash(token),))
        clear_session_cookie(response, request)
        return {"ok": True}

    @app.get("/api/agenda/auth/me")
    def agenda_me(request: Request):
        user = get_current_user(request)
        return {"user": public_user(user)}

    @app.get("/api/agenda/events")
    def agenda_list_events(request: Request, start: str | None = Query(default=None, max_length=64), end: str | None = Query(default=None, max_length=64)):
        user = get_current_user(request)
        clauses = ["user_id=?"]
        params: list[Any] = [user["id"]]
        if start:
            clauses.append("end_at>=?")
            params.append(parse_iso(start, "filtre début"))
        if end:
            clauses.append("start_at<?")
            params.append(parse_iso(end, "filtre fin"))
        with db_session() as conn:
            rows = conn.execute("SELECT * FROM agenda_events WHERE " + " AND ".join(clauses) + " ORDER BY start_at ASC LIMIT 2000", params).fetchall()
        return {"events": [event_row(row) for row in rows]}

    @app.post("/api/agenda/events")
    def agenda_create_event(body: EventBody, request: Request):
        same_origin_check(request)
        user = get_current_user(request)
        payload = clean_event_payload(body)
        event_id, now = str(uuid.uuid4()), iso_now()
        with db_session() as conn:
            conn.execute(
                "INSERT INTO agenda_events(id,user_id,title,start_at,end_at,description,location,category,color,all_day,timezone,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (event_id,user["id"],payload["title"],payload["start_at"],payload["end_at"],payload["description"],payload["location"],payload["category"],payload["color"],payload["all_day"],payload["timezone"],now,now),
            )
            row = conn.execute("SELECT * FROM agenda_events WHERE id=? AND user_id=?", (event_id,user["id"])).fetchone()
        return {"event": event_row(row)}

    @app.put("/api/agenda/events/{event_id}")
    def agenda_update_event(event_id: str, body: EventBody, request: Request):
        same_origin_check(request)
        user = get_current_user(request)
        payload = clean_event_payload(body)
        now = iso_now()
        with db_session() as conn:
            existing = conn.execute("SELECT id FROM agenda_events WHERE id=? AND user_id=?", (event_id,user["id"])).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Événement introuvable.")
            conn.execute(
                "UPDATE agenda_events SET title=?,start_at=?,end_at=?,description=?,location=?,category=?,color=?,all_day=?,timezone=?,updated_at=? WHERE id=? AND user_id=?",
                (payload["title"],payload["start_at"],payload["end_at"],payload["description"],payload["location"],payload["category"],payload["color"],payload["all_day"],payload["timezone"],now,event_id,user["id"]),
            )
            row = conn.execute("SELECT * FROM agenda_events WHERE id=? AND user_id=?", (event_id,user["id"])).fetchone()
        return {"event": event_row(row)}

    @app.delete("/api/agenda/events/{event_id}")
    def agenda_delete_event(event_id: str, request: Request):
        same_origin_check(request)
        user = get_current_user(request)
        with db_session() as conn:
            cur = conn.execute("DELETE FROM agenda_events WHERE id=? AND user_id=?", (event_id,user["id"]))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Événement introuvable.")
        return {"ok": True}

    @app.get("/api/agenda/health")
    def agenda_health():
        return {"status": "ok", "storage": "postgresql" if USE_POSTGRES else "sqlite", "account_sync": True, "apple_calendar_sync": False}
