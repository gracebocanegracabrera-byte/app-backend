"""
Tests ST-24 — Worker KPIs + Redis Pub/Sub.
Cubre: calculate_all(), publish_to_redis(), endpoint GET /dashboard/kpis.
"""
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.core.database import Base, get_db
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


async def _make_admin(client, db_session) -> str:
    """Registra admin y retorna JWT token."""
    reg = await client.post("/api/v1/auth/register", json={
        "email": "admin_kpi@test.com",
        "password": "pass1234",
        "name": "Admin KPI",
        "role": "admin",
        "legal_disclaimer_accepted": True,
        "privacy_policy_accepted": True,
        "data_processing_consent": True,
    })
    assert reg.status_code in (200, 201)
    login = await client.post("/api/v1/auth/login", json={
        "email": "admin_kpi@test.com",
        "password": "pass1234",
    })
    return login.json()["access_token"]


async def _make_buyer(client) -> str:
    """Registra buyer y retorna JWT token."""
    reg = await client.post("/api/v1/auth/register", json={
        "email": "buyer_kpi@test.com",
        "password": "pass1234",
        "name": "Buyer KPI",
        "role": "buyer",
        "legal_disclaimer_accepted": True,
        "privacy_policy_accepted": True,
        "data_processing_consent": True,
    })
    assert reg.status_code in (200, 201)
    login = await client.post("/api/v1/auth/login", json={
        "email": "buyer_kpi@test.com",
        "password": "pass1234",
    })
    return login.json()["access_token"]


# ── Tests KPIWorker.calculate_all() ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_calculate_all_returns_expected_structure(db_session):
    """calculate_all retorna dict con claves global, a1, a2, a3, a4, a5, timestamp."""
    from app.services.kpi_worker import KPIWorker
    worker = KPIWorker()
    kpis = await worker.calculate_all(db_session)

    assert "timestamp" in kpis
    assert "global" in kpis
    assert "a1" in kpis
    assert "a2" in kpis
    assert "a3" in kpis
    assert "a4" in kpis
    assert "a5" in kpis


@pytest.mark.asyncio
async def test_calculate_all_global_keys(db_session):
    """global contiene todos los KPIs esperados con valores >= 0."""
    from app.services.kpi_worker import KPIWorker
    worker = KPIWorker()
    kpis = await worker.calculate_all(db_session)

    g = kpis["global"]
    assert "users_registered" in g
    assert "profiles_generated" in g
    assert "properties_analyzed" in g
    assert "evaluations_done" in g
    assert "rankings_generated" in g
    assert "appointments_made" in g
    for v in g.values():
        assert v >= 0


@pytest.mark.asyncio
async def test_calculate_all_a3_keys(db_session):
    """a3 contiene profiles_created, avg_interactions, avg_completeness_pct."""
    from app.services.kpi_worker import KPIWorker
    worker = KPIWorker()
    kpis = await worker.calculate_all(db_session)

    a3 = kpis["a3"]
    assert "profiles_created" in a3
    assert "avg_interactions" in a3
    assert "avg_completeness_pct" in a3
    # avg_completeness_pct 0.0 cuando no hay datos
    assert a3["avg_completeness_pct"] == 0.0


@pytest.mark.asyncio
async def test_calculate_all_a1_last_updated_none_when_empty(db_session):
    """a1.last_updated es None si no hay propiedades."""
    from app.services.kpi_worker import KPIWorker
    worker = KPIWorker()
    kpis = await worker.calculate_all(db_session)
    assert kpis["a1"]["last_updated"] is None


@pytest.mark.asyncio
async def test_calculate_all_saves_snapshots(client, db_session):
    """
    calculate_all guarda KpiSnapshot en BD para histórico — pero solo
    persiste métricas con valor > 0 que cambiaron desde el último guardado
    (ver `_should_persist`: evita basura de ceros en cada ciclo de 30s).

    Sembramos un usuario primero para garantizar al menos una métrica
    (`users_registered`) > 0 que debe quedar persistida.
    """
    from sqlalchemy import select, func
    from app.services.kpi_worker import KPIWorker
    from app.models.kpi import KpiSnapshot

    await _make_admin(client, db_session)

    worker = KPIWorker()
    await worker.calculate_all(db_session)

    count = (await db_session.execute(select(func.count()).select_from(KpiSnapshot))).scalar()
    # Debe haber guardado al menos la métrica no-cero (users_registered)
    assert count > 0


@pytest.mark.asyncio
async def test_calculate_all_a2_semaforo_keys(db_session):
    """a2 contiene evaluations_done, green, yellow, red."""
    from app.services.kpi_worker import KPIWorker
    worker = KPIWorker()
    kpis = await worker.calculate_all(db_session)

    a2 = kpis["a2"]
    assert "evaluations_done" in a2
    assert "green" in a2
    assert "yellow" in a2
    assert "red" in a2


# ── Tests publish_to_redis() ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_publish_to_redis_sets_and_publishes():
    """publish_to_redis llama SET kpi:latest y PUBLISH kpi:updates."""
    from app.services.kpi_worker import KPIWorker

    sample_kpis = {"timestamp": "2026-01-01T00:00:00", "global": {"users_registered": 5}}
    mock_redis = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch("app.services.kpi_worker.aioredis.from_url", return_value=mock_redis):
        worker = KPIWorker()
        await worker.publish_to_redis(sample_kpis)

    mock_redis.set.assert_called_once_with("kpi:latest", json.dumps(sample_kpis))
    mock_redis.publish.assert_called_once_with("kpi:updates", json.dumps(sample_kpis))
    mock_redis.aclose.assert_called_once()


@pytest.mark.asyncio
async def test_publish_to_redis_closes_connection_on_error():
    """aclose() se llama aunque publish falle (finally block)."""
    from app.services.kpi_worker import KPIWorker

    mock_redis = AsyncMock()
    mock_redis.publish.side_effect = Exception("Redis down")
    mock_redis.aclose = AsyncMock()

    with patch("app.services.kpi_worker.aioredis.from_url", return_value=mock_redis):
        worker = KPIWorker()
        with pytest.raises(Exception, match="Redis down"):
            await worker.publish_to_redis({"timestamp": "now"})

    mock_redis.aclose.assert_called_once()


# ── Tests GET /dashboard/kpis ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_kpis_requires_auth(client):
    """Sin token → 403."""
    resp = await client.get("/api/v1/dashboard/kpis")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_kpis_buyer_forbidden(client):
    """Buyer no puede acceder → 403."""
    token = await _make_buyer(client)
    resp = await client.get(
        "/api/v1/dashboard/kpis",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_kpis_admin_returns_data(client):
    """Admin con Redis que tiene kpi:latest → retorna JSON con estructura correcta."""
    token = await _make_admin(client, None)
    sample = {"timestamp": "2026-06-06T00:00:00", "global": {"users_registered": 3}}

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=json.dumps(sample))
    mock_redis.aclose = AsyncMock()

    with patch("app.api.v1.dashboard.aioredis.from_url", return_value=mock_redis):
        resp = await client.get(
            "/api/v1/dashboard/kpis",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["timestamp"] == "2026-06-06T00:00:00"
    assert data["global"]["users_registered"] == 3


@pytest.mark.asyncio
async def test_get_kpis_no_data_yet(client):
    """Redis vacío (kpi:latest = None) → 200 con mensaje de espera."""
    token = await _make_admin(client, None)

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.aclose = AsyncMock()

    with patch("app.api.v1.dashboard.aioredis.from_url", return_value=mock_redis):
        resp = await client.get(
            "/api/v1/dashboard/kpis",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert "message" in resp.json()


@pytest.mark.asyncio
async def test_get_kpis_redis_error_returns_503(client):
    """Redis connection error → 503."""
    token = await _make_admin(client, None)

    mock_redis = AsyncMock()
    mock_redis.get.side_effect = Exception("Connection refused")
    mock_redis.aclose = AsyncMock()

    with patch("app.api.v1.dashboard.aioredis.from_url", return_value=mock_redis):
        resp = await client.get(
            "/api/v1/dashboard/kpis",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 503
