"""
Tests ST-22 — Comunicaciones IA Personalizadas (A5).
Cubre: GET/PATCH /notifications, POST /notify, notificación en appointments y leads.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from uuid import uuid4

from app.core.database import Base, get_db
from app.models.property import Property
from main import app

_MOCK_AI_MESSAGE = "Estimado/a Cliente, le informamos sobre su solicitud. Estamos a su disposición."


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture(autouse=True)
def mock_openrouter():
    """Mock OpenRouter — todos los tests usan este mock para ai_complete."""
    with patch("app.core.ai_gateway._client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content=_MOCK_AI_MESSAGE))]
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
        json={"email": "buyer22@test.com", "name": "Buyer22", "password": "secret123", "role": "buyer"},
    )
    return resp.json()["access_token"]


@pytest.fixture
async def buyer2_token(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "buyer22b@test.com", "name": "Buyer22B", "password": "secret123", "role": "buyer"},
    )
    return resp.json()["access_token"]


@pytest.fixture
async def admin_token(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "admin22@test.com", "name": "Admin22", "password": "secret123", "role": "admin"},
    )
    return resp.json()["access_token"]


@pytest.fixture
async def seed_property(db_session):
    prop = Property(
        id=uuid4(),
        title="Casa Test ST-22",
        price=350000,
        district="trujillo",
        property_type="casa",
        listing_type="sale",
        is_active=True,
        source_name="mock",
    )
    db_session.add(prop)
    await db_session.commit()
    return {"id": str(prop.id), "title": prop.title}


# ─── GET /notifications ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_notifications_empty(client, buyer_token):
    resp = await client.get(
        "/api/v1/agents/a5/notifications",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_notifications_requires_auth(client):
    resp = await client.get("/api/v1/agents/a5/notifications")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_list_notifications_returns_own_only(client, buyer_token, buyer2_token):
    """Buyer ve solo sus notificaciones, no las de otros."""
    # Crear notificación para buyer con POST /notify
    await client.post(
        "/api/v1/agents/a5/notify",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"title": "Notif Buyer1", "message": "Mensaje para buyer1", "type": "info"},
    )
    await client.post(
        "/api/v1/agents/a5/notify",
        headers={"Authorization": f"Bearer {buyer2_token}"},
        json={"title": "Notif Buyer2", "message": "Mensaje para buyer2", "type": "info"},
    )

    resp = await client.get(
        "/api/v1/agents/a5/notifications",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "Notif Buyer1"


# ─── POST /notify (manual demo) ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notify_manual_creates_notification(client, buyer_token):
    resp = await client.post(
        "/api/v1/agents/a5/notify",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"title": "Test Notif", "message": "Mensaje de prueba", "type": "success"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Test Notif"
    assert data["message"] == "Mensaje de prueba"
    assert data["type"] == "success"
    assert data["read"] is False
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_notify_manual_invalid_type_returns_422(client, buyer_token):
    """type debe ser info | success | warning — cualquier otro → 422."""
    resp = await client.post(
        "/api/v1/agents/a5/notify",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"title": "Bad Type", "message": "msg", "type": "INVALID"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_notify_manual_default_type_info(client, buyer_token):
    resp = await client.post(
        "/api/v1/agents/a5/notify",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"title": "Default Type", "message": "msg"},
    )
    assert resp.status_code == 201
    assert resp.json()["type"] == "info"


# ─── PATCH /notifications/{id}/read ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_notification_read(client, buyer_token):
    create_resp = await client.post(
        "/api/v1/agents/a5/notify",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"title": "Mark Test", "message": "msg", "type": "info"},
    )
    notif_id = create_resp.json()["id"]

    patch_resp = await client.patch(
        f"/api/v1/agents/a5/notifications/{notif_id}/read",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert patch_resp.status_code == 204

    # Verificar que aparece como leída en el listado
    list_resp = await client.get(
        "/api/v1/agents/a5/notifications",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    found = next(n for n in list_resp.json() if n["id"] == notif_id)
    assert found["read"] is True


@pytest.mark.asyncio
async def test_mark_notification_read_404_nonexistent(client, buyer_token):
    fake_id = str(uuid4())
    resp = await client.patch(
        f"/api/v1/agents/a5/notifications/{fake_id}/read",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mark_notification_read_403_wrong_user(client, buyer_token, buyer2_token):
    """buyer2 no puede marcar leída la notificación de buyer1 → 403, no 404."""
    create_resp = await client.post(
        "/api/v1/agents/a5/notify",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"title": "Private", "message": "msg", "type": "info"},
    )
    notif_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/agents/a5/notifications/{notif_id}/read",
        headers={"Authorization": f"Bearer {buyer2_token}"},
    )
    assert resp.status_code == 403


# ─── Notificación automática — POST /appointments ────────────────────────────


@pytest.mark.asyncio
async def test_appointment_creates_notification(client, buyer_token, seed_property):
    """POST /appointments → debe crear notificación 'Cita agendada' para el usuario."""
    prop_id = seed_property["id"]

    resp = await client.post(
        "/api/v1/agents/a5/appointments",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={
            "property_id": prop_id,
            "scheduled_at": "2026-07-15T10:00:00+00:00",
        },
    )
    assert resp.status_code == 201

    notif_resp = await client.get(
        "/api/v1/agents/a5/notifications",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    notifications = notif_resp.json()
    assert len(notifications) >= 1
    titles = [n["title"] for n in notifications]
    assert any("agendada" in t.lower() or "cita" in t.lower() for t in titles)


# ─── Notificación automática — PATCH /leads/{id} ─────────────────────────────


@pytest.mark.asyncio
async def test_lead_status_change_creates_notification(
    client, buyer_token, admin_token, seed_property
):
    """PATCH /leads/{id} con status='contacted' → notificación para el buyer."""
    prop_id = seed_property["id"]

    # Crear lead como buyer
    create_resp = await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id},
    )
    assert create_resp.status_code == 201
    lead_id = create_resp.json()["id"]

    # Admin cambia status → dispara notificación al buyer
    patch_resp = await client.patch(
        f"/api/v1/agents/a5/leads/{lead_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "contacted"},
    )
    assert patch_resp.status_code == 200

    # Buyer debe tener notificación
    notif_resp = await client.get(
        "/api/v1/agents/a5/notifications",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert len(notif_resp.json()) >= 1


@pytest.mark.asyncio
async def test_lead_status_new_does_not_create_notification(
    client, buyer_token, admin_token, seed_property
):
    """Status 'new' no está en NOTIFY_STATUSES → no crea notificación."""
    prop_id = seed_property["id"]

    create_resp = await client.post(
        "/api/v1/agents/a5/leads",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"property_id": prop_id},
    )
    lead_id = create_resp.json()["id"]

    await client.patch(
        f"/api/v1/agents/a5/leads/{lead_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "new"},
    )

    notif_resp = await client.get(
        "/api/v1/agents/a5/notifications",
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert len(notif_resp.json()) == 0
