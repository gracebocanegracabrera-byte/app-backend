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


@pytest.fixture(autouse=True)
def mock_openrouter():
    with patch("app.core.ai_gateway._client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content="Hola, cuéntame más"))]
            )
        )
        yield mock_client.chat.completions.create


@pytest.fixture
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as session:
        yield session


@pytest.fixture
async def client(db_session):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def buyer_token(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "buyer@test.com", "name": "Buyer", "password": "secret123", "role": "buyer"},
    )
    return resp.json()["access_token"]


@pytest.fixture
async def admin_token(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "admin@test.com", "name": "Admin", "password": "secret123", "role": "admin"},
    )
    return resp.json()["access_token"]


@pytest.fixture
async def advisor_token(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "advisor@test.com", "name": "Advisor", "password": "secret123", "role": "advisor"},
    )
    return resp.json()["access_token"]


# ─── Auth protection ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scrape_requires_auth(client):
    resp = await client.post("/api/v1/agents/a1/scrape")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_scrape_rejects_buyer(client, buyer_token):
    resp = await client.post(
        "/api/v1/agents/a1/scrape",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_status_requires_auth(client):
    resp = await client.get("/api/v1/agents/a1/status")
    assert resp.status_code in (401, 403)


# ─── Scrape endpoint ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scrape_imports_mock_properties(client, admin_token):
    resp = await client.post(
        "/api/v1/agents/a1/scrape",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] == 30
    assert data["total"] == 30
    assert "Importadas" in data["message"]


@pytest.mark.asyncio
async def test_scrape_dedup_does_not_reimport(client, admin_token):
    resp1 = await client.post(
        "/api/v1/agents/a1/scrape",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp1.json()["imported"] == 30

    resp2 = await client.post(
        "/api/v1/agents/a1/scrape",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["imported"] == 0
    assert resp2.json()["total"] == 30


@pytest.mark.asyncio
async def test_scrape_allows_advisor(client, advisor_token):
    resp = await client.post(
        "/api/v1/agents/a1/scrape",
        headers={"Authorization": f"Bearer {advisor_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["imported"] == 30


# ─── Status endpoint ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_returns_counts_after_scrape(client, admin_token):
    await client.post(
        "/api/v1/agents/a1/scrape",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    resp = await client.get(
        "/api/v1/agents/a1/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_properties"] == 30
    assert data["last_updated"] is not None
    assert "mock" in data["sources"]


@pytest.mark.asyncio
async def test_status_zero_when_no_scrape(client, admin_token):
    resp = await client.get(
        "/api/v1/agents/a1/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["active_properties"] == 0
