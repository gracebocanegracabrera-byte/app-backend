"""
Tests ST-27 — Endpoint histórico de KPIs (GET /dashboard/history).

Cubre:
- Auth requerida (401 sin token)
- Rol requerido (buyer rechazado, admin/advisor permitido)
- Filtro por agent + metric_name
- Orden cronológico ascendente y límite
"""
import pytest
from datetime import datetime, timedelta, timezone

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.core.database import Base, get_db
from app.models.kpi import KpiSnapshot
from main import app


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def client(db_session):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _make_admin(client) -> str:
    reg = await client.post("/api/v1/auth/register", json={
        "email": "admin_hist@test.com",
        "password": "pass1234",
        "name": "Admin Hist",
        "role": "admin",
        "legal_disclaimer_accepted": True,
        "privacy_policy_accepted": True,
        "data_processing_consent": True,
    })
    assert reg.status_code in (200, 201)
    login = await client.post("/api/v1/auth/login", json={
        "email": "admin_hist@test.com",
        "password": "pass1234",
    })
    return login.json()["access_token"]


async def _make_advisor(client) -> str:
    reg = await client.post("/api/v1/auth/register", json={
        "email": "advisor_hist@test.com",
        "password": "pass1234",
        "name": "Advisor Hist",
        "role": "advisor",
        "legal_disclaimer_accepted": True,
        "privacy_policy_accepted": True,
        "data_processing_consent": True,
    })
    assert reg.status_code in (200, 201)
    login = await client.post("/api/v1/auth/login", json={
        "email": "advisor_hist@test.com",
        "password": "pass1234",
    })
    return login.json()["access_token"]


async def _make_buyer(client) -> str:
    reg = await client.post("/api/v1/auth/register", json={
        "email": "buyer_hist@test.com",
        "password": "pass1234",
        "name": "Buyer Hist",
        "role": "buyer",
        "legal_disclaimer_accepted": True,
        "privacy_policy_accepted": True,
        "data_processing_consent": True,
    })
    assert reg.status_code in (200, 201)
    login = await client.post("/api/v1/auth/login", json={
        "email": "buyer_hist@test.com",
        "password": "pass1234",
    })
    return login.json()["access_token"]


async def _seed_snapshots(db_session):
    """Crea snapshots con recorded_at crecientes para 'global/users_registered'
    y un snapshot de otro agente/métrica para verificar filtrado."""
    base = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    for i, value in enumerate([1.0, 2.0, 3.0, 4.0, 5.0]):
        db_session.add(KpiSnapshot(
            agent="global",
            metric_name="users_registered",
            value=value,
            recorded_at=base + timedelta(minutes=i),
        ))
    # Ruido — no debe aparecer en el filtro agent=global metric=users_registered
    db_session.add(KpiSnapshot(
        agent="a1",
        metric_name="properties_collected",
        value=99.0,
        recorded_at=base,
    ))
    await db_session.commit()


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_history_requires_auth(client):
    """Sin token → 403 (HTTPBearer rechaza, igual que /dashboard/kpis)."""
    resp = await client.get("/api/v1/dashboard/history")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_history_buyer_forbidden(client):
    token = await _make_buyer(client)
    resp = await client.get(
        "/api/v1/dashboard/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_history_admin_returns_snapshots_in_chronological_order(client, db_session):
    token = await _make_admin(client)
    await _seed_snapshots(db_session)

    resp = await client.get(
        "/api/v1/dashboard/history",
        params={"agent": "global", "metric": "users_registered", "limit": 50},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 5
    # orden cronológico ascendente (oldest -> newest)
    values = [d["value"] for d in data]
    assert values == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert all("recorded_at" in d for d in data)


@pytest.mark.asyncio
async def test_history_advisor_allowed_and_filters_by_agent_metric(client, db_session):
    token = await _make_advisor(client)
    await _seed_snapshots(db_session)

    resp = await client.get(
        "/api/v1/dashboard/history",
        params={"agent": "a1", "metric": "properties_collected", "limit": 50},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["value"] == 99.0


@pytest.mark.asyncio
async def test_history_respects_limit(client, db_session):
    token = await _make_admin(client)
    await _seed_snapshots(db_session)

    resp = await client.get(
        "/api/v1/dashboard/history",
        params={"agent": "global", "metric": "users_registered", "limit": 2},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    # con limit=2, se devuelven los 2 más recientes pero en orden cronológico
    values = [d["value"] for d in data]
    assert values == [4.0, 5.0]


@pytest.mark.asyncio
async def test_history_empty_when_no_snapshots(client):
    token = await _make_admin(client)

    resp = await client.get(
        "/api/v1/dashboard/history",
        params={"agent": "global", "metric": "users_registered"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []
