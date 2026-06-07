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


# Respuesta neutral — sin keywords de confirmación
_NEUTRAL_RESPONSE = "¿Cuándo le gustaría visitar la propiedad?"


@pytest.fixture(autouse=True)
def mock_openrouter():
    with patch("app.core.ai_gateway._client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content=_NEUTRAL_RESPONSE))]
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
    """H1 fix: inserta propiedades directamente en DB — sin red ni scraper."""
    props = [
        Property(
            id=uuid4(),
            title=f"Propiedad Scheduling {i}",
            price=300000,
            district="trujillo",
            property_type="departamento",
            listing_type="sale",
            is_active=True,
            source_name="mock",
        )
        for i in range(1, 4)  # 3 props para tests que necesitan [0], [1]
    ]
    for p in props:
        db_session.add(p)
    await db_session.commit()
    return [{"id": str(p.id), "title": p.title} for p in props]


# ─── CHAT AGENDAMIENTO ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_requires_auth(client):
    resp = await client.post(
        "/api/v1/agents/a5/chat",
        json={"property_id": "00000000-0000-0000-0000-000000000001", "messages": []},
    )
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_chat_returns_message_from_a5(client, buyer_token, seed_property):
    """m1 fix: mock retorna respuesta neutral (sin keywords confirmación)."""
    prop_id = seed_property[0]["id"]
    resp = await client.post(
        "/api/v1/agents/a5/chat",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={
            "property_id": prop_id,
            "messages": [{"role": "user", "content": "Hola, ¿cuándo puedo visitar?"}],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "message" in data
    assert data["message"] == _NEUTRAL_RESPONSE
    assert data["appointment_created"] is False
    assert data["appointment"] is None


@pytest.mark.asyncio
async def test_chat_with_confirmation_auto_books(client, buyer_token, seed_property, mock_openrouter):
    """
    m1 fix + C1 test: cuando A5 confirma Y ai_json extrae fecha válida → appointment_created=True.
    Usa side_effect para devolver: 1ra llamada = confirmación, 2da = JSON con fecha.
    """
    prop_id = seed_property[0]["id"]

    call_count = 0

    async def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Primera llamada: ai_complete para respuesta del chat
            return MagicMock(
                choices=[MagicMock(message=MagicMock(content="Confirmado, quedamos para el martes a las 10."))]
            )
        else:
            # Segunda llamada: ai_json extrae fecha
            return MagicMock(
                choices=[MagicMock(message=MagicMock(content='{"date": "2026-06-16", "hour": 10}'))]
            )

    mock_openrouter.side_effect = side_effect

    resp = await client.post(
        "/api/v1/agents/a5/chat",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={
            "property_id": prop_id,
            "messages": [
                {"role": "user", "content": "Quiero visitar el martes a las 10"},
                {"role": "assistant", "content": "Perfecto, ¿confirma el martes 16/06 a las 10:00?"},
                {"role": "user", "content": "Sí, confirmo"},
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["appointment_created"] is True
    assert data["appointment"] is not None
    assert data["appointment"]["status"] == "pending"


@pytest.mark.asyncio
async def test_chat_404_for_nonexistent_property(client, buyer_token):
    resp = await client.post(
        "/api/v1/agents/a5/chat",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={
            "property_id": "00000000-0000-0000-0000-000000000000",
            "messages": [{"role": "user", "content": "Hola"}],
        },
    )
    assert resp.status_code == 404


# ─── APPOINTMENT AUTO-LEAD ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_appointment_auto_creates_lead(client, buyer_token, seed_property):
    prop_id = seed_property[0]["id"]
    resp = await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={
            "property_id": prop_id,
            "scheduled_at": "2026-06-10T10:00:00Z",
        },
    )
    assert resp.status_code == 201

    leads = await client.get(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert leads.status_code == 200
    assert len(leads.json()) == 1
    assert leads.json()[0]["property_id"] == prop_id


@pytest.mark.asyncio
async def test_create_appointment_sets_lead_to_scheduled(client, buyer_token, seed_property):
    prop_id = seed_property[0]["id"]
    await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={
            "property_id": prop_id,
            "scheduled_at": "2026-06-10T11:00:00Z",
        },
    )

    leads = await client.get(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert leads.json()[0]["status"] == "scheduled"


@pytest.mark.asyncio
async def test_create_appointment_reuses_existing_lead(client, buyer_token, seed_property):
    prop_id = seed_property[0]["id"]

    await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id},
    )
    leads_before = await client.get(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    lead_id_before = leads_before.json()[0]["id"]

    await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={
            "property_id": prop_id,
            "scheduled_at": "2026-06-10T12:00:00Z",
        },
    )

    leads_after = await client.get(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert len(leads_after.json()) == 1
    assert leads_after.json()[0]["id"] == lead_id_before


# ─── LIST APPOINTMENTS ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_appointments_requires_auth(client):
    resp = await client.get("/api/v1/agents/a5/appointments")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_list_appointments_buyer_sees_own(client, buyer_token, admin_token, seed_property):
    prop1 = seed_property[0]["id"]
    prop2 = seed_property[1]["id"]

    await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop1, "scheduled_at": "2026-06-10T10:00:00Z"},
    )
    await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"property_id": prop2, "scheduled_at": "2026-06-11T10:00:00Z"},
    )

    resp = await client.get(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_list_appointments_admin_sees_all(client, buyer_token, admin_token, seed_property):
    prop1 = seed_property[0]["id"]
    prop2 = seed_property[1]["id"]

    await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop1, "scheduled_at": "2026-06-10T10:00:00Z"},
    )
    await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"property_id": prop2, "scheduled_at": "2026-06-11T10:00:00Z"},
    )

    resp = await client.get(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_list_appointments_advisor_sees_all(client, buyer_token, advisor_token, seed_property):
    prop1 = seed_property[0]["id"]
    prop2 = seed_property[1]["id"]

    await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop1, "scheduled_at": "2026-06-10T10:00:00Z"},
    )
    await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {advisor_token}"},
        json={"property_id": prop2, "scheduled_at": "2026-06-11T10:00:00Z"},
    )

    resp = await client.get(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {advisor_token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# ─── PATCH APPOINTMENT ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_appointment_status(client, buyer_token, seed_property):
    prop_id = seed_property[0]["id"]
    create_resp = await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id, "scheduled_at": "2026-06-10T10:00:00Z"},
    )
    appt_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/agents/a5/appointments/{appt_id}?status=confirmed",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"


@pytest.mark.asyncio
async def test_update_appointment_to_completed_sets_lead_visited(client, buyer_token, seed_property):
    prop_id = seed_property[0]["id"]
    create_resp = await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id, "scheduled_at": "2026-06-10T10:00:00Z"},
    )
    appt_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/agents/a5/appointments/{appt_id}?status=completed",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"

    leads = await client.get(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert leads.json()[0]["status"] == "visited"


@pytest.mark.asyncio
async def test_update_appointment_invalid_status_returns_422(client, buyer_token, seed_property):
    """m2 fix: status inválido ahora retorna 422 (Literal validation) no 400."""
    prop_id = seed_property[0]["id"]
    create_resp = await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id, "scheduled_at": "2026-06-10T10:00:00Z"},
    )
    appt_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/agents/a5/appointments/{appt_id}?status=invalid_status",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_appointment_not_found(client, buyer_token):
    resp = await client.patch(
        "/api/v1/agents/a5/appointments/00000000-0000-0000-0000-000000000001?status=confirmed",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_buyer_cannot_update_other_users_appointment(
    client, buyer_token, admin_token, seed_property
):
    """C2 fix: buyer no puede modificar cita de otro usuario."""
    prop_id = seed_property[0]["id"]
    admin_appt = await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"property_id": prop_id, "scheduled_at": "2026-06-20T14:00:00Z"},
    )
    appt_id = admin_appt.json()["id"]

    resp = await client.patch(
        f"/api/v1/agents/a5/appointments/{appt_id}?status=confirmed",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_advisor_can_update_any_appointment(
    client, buyer_token, advisor_token, seed_property
):
    """C2 fix: advisor puede modificar cualquier cita."""
    prop_id = seed_property[0]["id"]
    buyer_appt = await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id, "scheduled_at": "2026-06-20T15:00:00Z"},
    )
    appt_id = buyer_appt.json()["id"]

    resp = await client.patch(
        f"/api/v1/agents/a5/appointments/{appt_id}?status=confirmed",
        headers={"Authorization": f"Bearer {advisor_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"


# ─── SLOT CONFLICT ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_appointment_slot_taken_returns_409_with_alternatives(client, buyer_token, seed_property):
    prop_id = seed_property[0]["id"]

    await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id, "scheduled_at": "2026-06-10T10:00:00Z"},
    )

    resp = await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id, "scheduled_at": "2026-06-10T10:00:00Z"},
    )
    assert resp.status_code == 409
    error = resp.json()
    assert error["detail"]["detail"] == "slot_taken"
    assert "available_slots" in error["detail"]
