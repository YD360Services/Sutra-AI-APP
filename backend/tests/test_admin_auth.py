"""
Admin routes must accept the session token the apps actually send (`Authorization: Bearer
<login-token>`, stored as users.active_session_token). They used to accept only JWTs, which
nothing issues, so every admin call returned 401 and the web app fell back to treating
Admin-Console-added admins as free users (Top Up / token prompts during live sessions).

Isolated in-memory SQLite via a dependency override, like test_platform_health.py.
"""
import asyncio
import uuid

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.admins import router
from app.core.security import create_access_token
from app.db.database import Base, get_db
from app.db.models import AdminEmail, User

ADMIN_TOKEN = "33333333-aaaa-4bbb-8ccc-000000000003"
USER_TOKEN = "44444444-aaaa-4bbb-8ccc-000000000004"
ADMIN_ID = uuid.uuid4()


async def _make_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        s.add(User(id=ADMIN_ID, email="console-added@example.com", password_hash="x", active_session_token=ADMIN_TOKEN))
        s.add(User(id=uuid.uuid4(), email="user@example.com", password_hash="x", active_session_token=USER_TOKEN))
        s.add(AdminEmail(email="console-added@example.com"))
        await s.commit()

    async def override_db():
        async with Session() as s:
            yield s

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = override_db
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test"), engine


def _run(fn):
    async def wrapper():
        client, engine = await _make_client()
        try:
            await fn(client)
        finally:
            await client.aclose()
            await engine.dispose()
    asyncio.run(wrapper())


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_admin_session_token_can_list_admins():
    async def t(c):
        r = await c.get("/api/admins", headers=_auth(ADMIN_TOKEN))
        assert r.status_code == 200, r.text
        assert "console-added@example.com" in r.json()
    _run(t)


def test_non_admin_session_token_is_forbidden():
    async def t(c):
        r = await c.get("/api/admins", headers=_auth(USER_TOKEN))
        assert r.status_code == 403, r.text
    _run(t)


def test_missing_or_unknown_token_is_unauthorized():
    async def t(c):
        assert (await c.get("/api/admins")).status_code == 401
        assert (await c.get("/api/admins", headers=_auth(str(uuid.uuid4())))).status_code == 401
        assert (await c.get("/api/admins", headers=_auth("short"))).status_code == 401
    _run(t)


def test_jwt_still_accepted():
    async def t(c):
        jwt = create_access_token(str(ADMIN_ID))
        r = await c.get("/api/admins", headers=_auth(jwt))
        assert r.status_code == 200, r.text
    _run(t)
