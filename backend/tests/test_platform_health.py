"""
Tests for /api/platform-health. Uses an isolated in-memory SQLite database via a dependency
override, so they never touch the configured DATABASE_URL. Plain functions driving asyncio,
so they run under pytest without pytest-asyncio.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.platform_health import router
from app.db.database import Base, get_db
from app.db.models import AdminEmail, User

ADMIN_TOKEN = "11111111-aaaa-4bbb-8ccc-000000000001"
USER_TOKEN = "22222222-aaaa-4bbb-8ccc-000000000002"


async def _make_client(seed_admin_table: bool = True):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as s:
        s.add(User(id=uuid.uuid4(), email="boss@example.com", password_hash="x", active_session_token=ADMIN_TOKEN))
        s.add(User(id=uuid.uuid4(), email="user@example.com", password_hash="x", active_session_token=USER_TOKEN))
        if seed_admin_table:
            s.add(AdminEmail(email="boss@example.com"))
        await s.commit()

    async def override_db():
        async with Session() as s:
            yield s

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = override_db
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return client, engine


def _run(coro_fn, **kw):
    async def wrapper():
        client, engine = await _make_client(**kw)
        try:
            await coro_fn(client)
        finally:
            await client.aclose()
            await engine.dispose()
    asyncio.run(wrapper())


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat() + "Z"


def test_empty_by_default_nothing_fabricated():
    async def t(c):
        r = await c.get("/api/platform-health")
        assert r.status_code == 200
        assert r.json() == []
    _run(t)


def test_admin_can_record_and_everyone_can_read():
    async def t(c):
        checked = _iso(datetime.utcnow() - timedelta(hours=1))
        r = await c.put(
            "/api/platform-health/zoom",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={"status": "warning", "checkedAt": checked, "latencyMs": 3200, "error": "  Overlay flicker  ", "result": ""},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == "zoom" and body["status"] == "warning"
        assert body["error"] == "Overlay flicker"  # trimmed
        assert body["result"] is None  # blank -> null
        assert body["checkedAt"] == checked

        r = await c.get("/api/platform-health")  # no auth needed to read
        assert [p["id"] for p in r.json()] == ["zoom"]
        assert "updated_by" not in r.text and "boss@example.com" not in r.text  # admin email not exposed
    _run(t)


def test_rerecord_replaces_stale_fields():
    async def t(c):
        h = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
        now = _iso(datetime.utcnow())
        await c.put("/api/platform-health/teams", headers=h, json={"status": "failed", "checkedAt": now, "error": "boom", "latencyMs": 9000})
        r = await c.put("/api/platform-health/teams", headers=h, json={"status": "operational", "checkedAt": now})
        assert r.json()["error"] is None and r.json()["latencyMs"] is None
    _run(t)


def test_writes_require_admin_session_token():
    async def t(c):
        body = {"status": "operational", "checkedAt": _iso(datetime.utcnow())}
        assert (await c.put("/api/platform-health/zoom", json=body)).status_code == 401
        assert (await c.put("/api/platform-health/zoom", headers={"Authorization": "Bearer nope-not-a-real-token-000"}, json=body)).status_code == 401
        assert (await c.put("/api/platform-health/zoom", headers={"Authorization": f"Bearer {USER_TOKEN}"}, json=body)).status_code == 403
        # The spoofable header used elsewhere must not grant access here.
        assert (await c.put("/api/platform-health/zoom", headers={"X-Admin-Email": "boss@example.com"}, json=body)).status_code == 401
        assert (await c.delete("/api/platform-health/zoom", headers={"Authorization": f"Bearer {USER_TOKEN}"})).status_code == 403
        assert (await c.get("/api/platform-health")).json() == []
    _run(t)


def test_seed_list_fallback_when_admin_table_empty():
    # boss@example.com is not in INITIAL_ADMIN_EMAILS, so with an empty admin table it is still rejected.
    async def t(c):
        body = {"status": "operational", "checkedAt": _iso(datetime.utcnow())}
        r = await c.put("/api/platform-health/zoom", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}, json=body)
        assert r.status_code == 403
    _run(t, seed_admin_table=False)


def test_validation():
    async def t(c):
        h = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
        now = _iso(datetime.utcnow())
        assert (await c.put("/api/platform-health/myspace", headers=h, json={"status": "operational", "checkedAt": now})).status_code == 404
        assert (await c.put("/api/platform-health/zoom", headers=h, json={"status": "great", "checkedAt": now})).status_code == 422
        # completed outcomes need a check time; "testing" does not
        assert (await c.put("/api/platform-health/zoom", headers=h, json={"status": "operational"})).status_code == 422
        assert (await c.put("/api/platform-health/zoom", headers=h, json={"status": "testing"})).status_code == 200
        future = _iso(datetime.utcnow() + timedelta(days=1))
        assert (await c.put("/api/platform-health/zoom", headers=h, json={"status": "operational", "checkedAt": future})).status_code == 422
        assert (await c.put("/api/platform-health/zoom", headers=h, json={"status": "operational", "checkedAt": now, "latencyMs": -5})).status_code == 422
        assert (await c.put("/api/platform-health/zoom", headers=h, json={"status": "operational", "checkedAt": now, "error": "x" * 1001})).status_code == 422
    _run(t)


def test_clear_record():
    async def t(c):
        h = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
        await c.put("/api/platform-health/webex", headers=h, json={"status": "operational", "checkedAt": _iso(datetime.utcnow())})
        assert (await c.delete("/api/platform-health/webex", headers=h)).status_code == 204
        assert (await c.get("/api/platform-health")).json() == []
        assert (await c.delete("/api/platform-health/webex", headers=h)).status_code == 204  # idempotent
    _run(t)
