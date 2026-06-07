import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.core.database import Base, get_db
from main import app

# Make JSONB work with SQLite in tests
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
async def user_token(client):
    """Registra un usuario real y devuelve access_token."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "buyer@test.com",
            "name": "Test Buyer",
            "password": "secret123",
            "role": "buyer",
        },
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


# ─── Auth protection ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_requires_auth(client):
    resp = await client.post("/api/v1/agents/a3/chat", json={"message": "Hola"})
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_profile_requires_auth(client):
    resp = await client.get("/api/v1/agents/a3/profile")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_get_history_requires_auth(client):
    resp = await client.get("/api/v1/agents/a3/history")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_delete_history_requires_auth(client):
    resp = await client.delete("/api/v1/agents/a3/history")
    assert resp.status_code in (401, 403)


# ─── Chat endpoint ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_returns_response_and_completeness(client, user_token):
    token = user_token
    resp = await client.post(
        "/api/v1/agents/a3/chat",
        json={"message": "Hola, busco un departamento"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "response" in data
    assert "profile_completeness" in data
    assert data["response"] == "Hola, cuéntame más"


@pytest.mark.asyncio
async def test_chat_updates_completeness_after_few_messages(client, user_token):
    import json

    token = user_token
    profile_data = {
        "zone": "Victor Larco",
        "price_min": 200000,
        "price_max": 500000,
        "property_type": "departamento",
        "bedrooms": 3,
        "area_m2_min": 80,
        "purpose": "compra",
    }

    with patch("app.core.ai_gateway._client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[
                    MagicMock(message=MagicMock(content=json.dumps(profile_data)))
                ]
            )
        )
        for msg in ["Hola", "Busco en Victor Larco", "200-500k", "3 dorm"]:
            resp = await client.post(
                "/api/v1/agents/a3/chat",
                json={"message": msg},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["profile_completeness"] == 100.0


# ─── Profile endpoint ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_profile_returns_404_when_not_generated(client, user_token):
    token = user_token
    resp = await client.get(
        "/api/v1/agents/a3/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_profile_returns_data_after_chat(client, user_token):
    import json

    token = user_token
    profile_data = {
        "zone": "Victor Larco",
        "price_min": 200000,
        "price_max": 500000,
        "property_type": "departamento",
        "bedrooms": 3,
        "area_m2_min": 80,
        "purpose": "compra",
    }

    with patch("app.core.ai_gateway._client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[
                    MagicMock(message=MagicMock(content=json.dumps(profile_data)))
                ]
            )
        )
        for msg in ["Hola", "Busco en Victor Larco", "200-500k", "3 dorm"]:
            await client.post(
                "/api/v1/agents/a3/chat",
                json={"message": msg},
                headers={"Authorization": f"Bearer {token}"},
            )
        resp = await client.get(
            "/api/v1/agents/a3/profile",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["preferences"]["zone"] == "Victor Larco"
    assert data["completeness_pct"] == 100.0


# ─── History endpoint ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_history_returns_messages(client, user_token):
    token = user_token
    await client.post(
        "/api/v1/agents/a3/chat",
        json={"message": "Hola"},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(
        "/api/v1/agents/a3/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    roles = [m["role"] for m in data]
    assert "user" in roles
    assert "assistant" in roles
    user_msg = next(m for m in data if m["role"] == "user")
    assert user_msg["content"] == "Hola"


@pytest.mark.asyncio
async def test_delete_history_clears_messages(client, user_token):
    token = user_token
    await client.post(
        "/api/v1/agents/a3/chat",
        json={"message": "Hola"},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.delete(
        "/api/v1/agents/a3/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204

    resp = await client.get(
        "/api/v1/agents/a3/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.json() == []


# ─── PUT /profile endpoint ───────────────────────────────────────────────────


@pytest.fixture
async def user_with_profile(client):
    """Registra usuario, genera perfil via chat, retorna (token, profile_data)."""
    import json

    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "editor@test.com", "name": "Editor", "password": "secret123"},
    )
    token = resp.json()["access_token"]

    profile_data = {
        "zone": "Victor Larco", "price_min": 200000, "price_max": 500000,
        "property_type": "departamento", "bedrooms": 3,
        "area_m2_min": 80, "purpose": "compra",
    }
    with patch("app.core.ai_gateway._client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content=json.dumps(profile_data)))]
            )
        )
        for msg in ["Hola", "Victor Larco", "200-500k", "3 dorm"]:
            await client.post(
                "/api/v1/agents/a3/chat",
                json={"message": msg},
                headers={"Authorization": f"Bearer {token}"},
            )
    return token, profile_data


@pytest.mark.asyncio
async def test_put_profile_updates_field(client, user_with_profile):
    token, _ = user_with_profile
    resp = await client.put(
        "/api/v1/agents/a3/profile",
        json={"zone": "Huanchaco"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["preferences"]["zone"] == "Huanchaco"


@pytest.mark.asyncio
async def test_put_profile_persists_to_db(client, user_with_profile):
    """Verifica que el cambio se guardó en BD (no solo en memoria)."""
    token, _ = user_with_profile
    await client.put(
        "/api/v1/agents/a3/profile",
        json={"zone": "Victor Larco"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Leer de nuevo con GET para confirmar persistencia
    resp = await client.get(
        "/api/v1/agents/a3/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.json()["preferences"]["zone"] == "Victor Larco"


@pytest.mark.asyncio
async def test_put_profile_coerces_numeric_fields(client, user_with_profile):
    token, _ = user_with_profile
    resp = await client.put(
        "/api/v1/agents/a3/profile",
        json={"price_min": "300000", "bedrooms": "2"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    prefs = resp.json()["preferences"]
    assert prefs["price_min"] == 300000   # número, no string
    assert prefs["bedrooms"] == 2


@pytest.mark.asyncio
async def test_put_profile_ignores_invalid_fields(client, user_with_profile):
    token, _ = user_with_profile
    resp = await client.put(
        "/api/v1/agents/a3/profile",
        json={"zone": "Huanchaco", "hacked_field": "malicious"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    prefs = resp.json()["preferences"]
    assert "hacked_field" not in prefs
    assert prefs["zone"] == "Huanchaco"


@pytest.mark.asyncio
async def test_put_profile_404_when_no_profile(client, user_token):
    resp = await client.put(
        "/api/v1/agents/a3/profile",
        json={"zone": "Trujillo"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert resp.status_code == 404
