"""
GET /auth/google/callback must persist the user and their session token. It used to call
UserRepository methods that don't exist (get_user_by_firebase_uid / update_user), so the upsert
always failed "non-fatally": the browser got a loginToken the DB had never stored, and every
authenticated call afterwards (/api/admins, /api/users, ...) returned 401.

Google is stubbed by patching httpx.AsyncClient; DB is in-memory SQLite.
"""
import asyncio
import urllib.parse
import uuid

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.api.auth as auth_mod
from app.db.database import Base, get_db
from app.db.models import User

GOOGLE_SUB = "104784093337903014060"
EMAIL = "admin-person@example.com"


class _FakeGoogle:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kw):
        return httpx.Response(200, json={"access_token": "ya29.fake"})

    async def get(self, url, **kw):
        return httpx.Response(200, json={"sub": GOOGLE_SUB, "email": EMAIL, "name": "Admin Person"})


async def _run(monkeypatch, existing_user: bool):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    existing_id = uuid.uuid4()
    if existing_user:
        async with Session() as s:
            s.add(User(id=existing_id, email=EMAIL, password_hash="x", active_session_token="old-token"))
            await s.commit()

    async def override_db():
        async with Session() as s:
            yield s

    monkeypatch.setattr(auth_mod, "GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(auth_mod, "GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeGoogle)

    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api")
    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    # Build the client from the real class captured before patching.
    async with _RealClient(transport=transport, base_url="http://test") as c:
        res = await c.get("/api/auth/google/callback", params={"code": "abc", "state": "/"})

    assert res.status_code == 302, res.text
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(res.headers["location"]).query)
    assert "auth_error" not in qs, res.headers["location"]
    payload = auth_mod._verify_token(qs["roundmate_auth_token"][0])
    assert payload and payload["email"] == EMAIL

    async with Session() as s:
        user = (await s.execute(select(User).where(User.email == EMAIL))).scalar_one()
    assert user.active_session_token == payload["loginToken"]
    assert payload["id"] == str(user.id)
    if existing_user:
        assert user.id == existing_id
    else:
        assert user.id == uuid.uuid5(uuid.NAMESPACE_DNS, f"firebase:{GOOGLE_SUB}")


_RealClient = httpx.AsyncClient


def test_callback_creates_user_and_stores_session_token(monkeypatch):
    asyncio.run(_run(monkeypatch, existing_user=False))


def test_callback_updates_existing_user_session_token(monkeypatch):
    asyncio.run(_run(monkeypatch, existing_user=True))
