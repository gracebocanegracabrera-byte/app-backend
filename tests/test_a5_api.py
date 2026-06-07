import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from uuid import UUID, uuid4

from app.core.database import Base, get_db
from app.models.property import Property
from main import app


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture(autouse=True)
def mock_openrouter():
    with patch("app.core.ai_gateway._client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content="respuesta mock"))]
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


@pytest.fixture
async def seed_property(db_session):
    """Inserta 2 propiedades directamente en DB — no depende del scraper."""
    props = [
        Property(
            id=uuid4(),
            title=f"Propiedad Test {i}",
            price=250000,
            district="trujillo",
            property_type="departamento",
            listing_type="sale",
            is_active=True,
            source_name="mock",
        )
        for i in range(1, 3)
    ]
    for p in props:
        db_session.add(p)
    await db_session.commit()
    return [{"id": str(p.id), "title": p.title} for p in props]


# ─── Auth protection ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_lead_requires_auth(client):
    resp = await client.post(
        "/api/v1/agents/a5/leads",
        json={"property_id": "00000000-0000-0000-0000-000000000001"},
    )
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_list_leads_requires_auth(client):
    resp = await client.get("/api/v1/agents/a5/leads")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_update_lead_requires_auth(client):
    resp = await client.patch(
        "/api/v1/agents/a5/leads/00000000-0000-0000-0000-000000000001",
        json={"status": "contacted"},
    )
    assert resp.status_code in (401, 403)


# ─── Lead CRUD ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_lead(client, buyer_token, seed_property):
    prop_id = seed_property[0]["id"]

    resp = await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["property_id"] == prop_id
    assert data["status"] == "new"
    assert data["user_id"] is not None
    assert "id" in data


@pytest.mark.asyncio
async def test_create_lead_duplicate_returns_existing(client, buyer_token, seed_property):
    prop_id = seed_property[0]["id"]

    resp1 = await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id},
    )
    assert resp1.status_code == 201

    resp2 = await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id},
    )
    assert resp2.status_code == 201
    assert resp2.json()["id"] == resp1.json()["id"]


@pytest.mark.asyncio
async def test_list_leads_buyer_sees_own(client, buyer_token, admin_token, seed_property):
    props = seed_property
    prop1 = props[0]["id"]
    prop2 = props[1]["id"]

    await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop1},
    )
    await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"property_id": prop2},
    )

    resp = await client.get(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["property_id"] == prop1


@pytest.mark.asyncio
async def test_list_leads_admin_sees_all(client, buyer_token, admin_token, seed_property):
    props = seed_property
    prop1 = props[0]["id"]
    prop2 = props[1]["id"]

    await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop1},
    )
    await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"property_id": prop2},
    )

    resp = await client.get(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_list_leads_filter_by_property(client, buyer_token, admin_token, seed_property):
    props = seed_property
    prop1 = props[0]["id"]
    prop2 = props[1]["id"]

    await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"property_id": prop1},
    )
    await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"property_id": prop2},
    )

    resp = await client.get(
        f"/api/v1/agents/a5/leads?property_id={prop1}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["property_id"] == prop1


@pytest.mark.asyncio
async def test_update_lead_status(client, buyer_token, seed_property):
    prop_id = seed_property[0]["id"]

    create_resp = await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id},
    )
    lead_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/agents/a5/leads/{lead_id}",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"status": "interested", "notes": "Cliente muy interesado"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "interested"
    assert data["notes"] == "Cliente muy interesado"


@pytest.mark.asyncio
async def test_update_lead_not_found(client, buyer_token):
    resp = await client.patch(
        "/api/v1/agents/a5/leads/00000000-0000-0000-0000-000000000001",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"status": "contacted"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_buyer_cannot_update_other_lead(client, buyer_token, admin_token, seed_property):
    prop_id = seed_property[0]["id"]

    admin_lead = await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"property_id": prop_id},
    )
    lead_id = admin_lead.json()["id"]

    resp = await client.patch(
        f"/api/v1/agents/a5/leads/{lead_id}",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"status": "contacted"},
    )
    assert resp.status_code == 404


# ─── Appointments ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_appointment(client, buyer_token, seed_property):
    prop_id = seed_property[0]["id"]

    lead_resp = await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id},
    )
    lead_id = lead_resp.json()["id"]

    resp = await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={
            "lead_id": lead_id,
            "property_id": prop_id,
            "scheduled_at": "2026-06-10T10:00:00Z",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["lead_id"] == lead_id
    assert data["property_id"] == prop_id
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_appointment_slot_conflict(client, buyer_token, seed_property):
    prop_id = seed_property[0]["id"]

    lead_resp = await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id},
    )
    lead_id = lead_resp.json()["id"]

    await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={
            "lead_id": lead_id,
            "property_id": prop_id,
            "scheduled_at": "2026-06-10T10:00:00Z",
        },
    )

    lead2_resp = await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id},
    )
    lead2_id = lead2_resp.json()["id"]

    resp = await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={
            "lead_id": lead2_id,
            "property_id": prop_id,
            "scheduled_at": "2026-06-10T10:00:00Z",
        },
    )
    assert resp.status_code == 409
    error = resp.json()
    assert error["detail"]["detail"] == "slot_taken"
    assert "available_slots" in error["detail"]


@pytest.mark.asyncio
async def test_appointment_requires_auth(client):
    resp = await client.post(
        "/api/v1/agents/a5/appointments",
        json={
            "lead_id": "00000000-0000-0000-0000-000000000001",
            "property_id": "00000000-0000-0000-0000-000000000002",
            "scheduled_at": "2026-06-10T10:00:00Z",
        },
    )
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_appointment_ignores_lead_id_and_uses_current_user(client, buyer_token, admin_token, seed_property):
    """Lead del request body se ignora: el endpoint siempre usa el lead del current_user."""
    prop_id = seed_property[0]["id"]

    admin_lead = await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"property_id": prop_id},
    )
    admin_lead_id = admin_lead.json()["id"]

    resp = await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={
            "lead_id": admin_lead_id,
            "property_id": prop_id,
            "scheduled_at": "2026-06-15T11:00:00Z",
        },
    )
    assert resp.status_code == 201

    buyer_leads = await client.get(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert len(buyer_leads.json()) == 1
    assert buyer_leads.json()[0]["id"] != admin_lead_id


@pytest.mark.asyncio
async def test_advisor_sees_all_leads(client, buyer_token, advisor_token, seed_property):
    """Advisor ve todos los leads, no solo los propios."""
    prop1 = seed_property[0]["id"]
    prop2 = seed_property[1]["id"]

    await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop1},
    )
    await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {advisor_token}"},
        json={"property_id": prop2},
    )

    resp = await client.get(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {advisor_token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_advisor_can_update_any_lead(client, buyer_token, advisor_token, seed_property):
    """Advisor puede mover cualquier lead en el pipeline CRM."""
    prop_id = seed_property[0]["id"]

    buyer_lead = await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id},
    )
    lead_id = buyer_lead.json()["id"]

    resp = await client.patch(
        f"/api/v1/agents/a5/leads/{lead_id}",
        headers={"Authorization": f"Bearer {advisor_token}"},
        json={"status": "contacted", "notes": "Llamado el 06/06"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "contacted"


@pytest.mark.asyncio
async def test_update_lead_invalid_status_returns_422(client, buyer_token, seed_property):
    """Status inválido retorna 422, no 404."""
    prop_id = seed_property[0]["id"]

    create_resp = await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id},
    )
    lead_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/agents/a5/leads/{lead_id}",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"status": "banana"},
    )
    assert resp.status_code == 422
