"""
Tests ST-25 — WebSocket Endpoint Dashboard.

Cubre:
- Auth via query param token (válido / inválido / sin token)
- Role check (admin/advisor permitido, buyer rechazado)
- Snapshot inicial desde kpi:latest
- Reenvío de mensajes publicados en kpi:updates
- Cierre limpio al desconectar el cliente
- Manejo de errores de Redis
"""
import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.core.auth import create_access_token
from app.core.database import Base, get_db
from main import app


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def client(engine):
    """TestClient (sync) con DB SQLite in-memory compartida via StaticPool."""
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def _override():
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as session:
            yield session

    loop.run_until_complete(_init())
    app.dependency_overrides[get_db] = _override

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()

    async def _drop():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    loop.run_until_complete(_drop())
    loop.run_until_complete(engine.dispose())
    loop.close()


def _admin_token() -> str:
    return create_access_token("11111111-1111-1111-1111-111111111111", "admin")


def _advisor_token() -> str:
    return create_access_token("22222222-2222-2222-2222-222222222222", "advisor")


def _buyer_token() -> str:
    return create_access_token("33333333-3333-3333-3333-333333333333", "buyer")


# ── Auth tests ──────────────────────────────────────────────────────────────


def test_ws_no_token_rejected(client):
    """Sin token → cierra con 1008."""
    with pytest.raises(Exception):
        with client.websocket_connect("/api/v1/dashboard/ws") as ws:
            ws.receive_text()


def test_ws_invalid_token_rejected(client):
    """Token inválido → cierra con 1008."""
    with pytest.raises(Exception):
        with client.websocket_connect("/api/v1/dashboard/ws?token=invalid_jwt_xyz") as ws:
            ws.receive_text()


def test_ws_buyer_rejected(client):
    """Buyer (rol no permitido) → cierra con 1008."""
    token = _buyer_token()
    with pytest.raises(Exception):
        with client.websocket_connect(f"/api/v1/dashboard/ws?token={token}") as ws:
            ws.receive_text()


# ── Snapshot inicial ────────────────────────────────────────────────────────


def test_ws_admin_receives_initial_snapshot(client):
    """Al conectar, envía kpi:latest si existe en Redis."""
    token = _admin_token()
    sample = {"timestamp": "2026-06-06T00:00:00", "global": {"users_registered": 5}}

    # Mock Redis: get devuelve bytes, pubsub no publica nada
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=json.dumps(sample).encode())
    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()
    # get_message retorna None siempre (no hay updates en este test)
    pubsub.get_message = AsyncMock(return_value=None)
    mock_redis.pubsub = MagicMock(return_value=pubsub)
    mock_redis.aclose = AsyncMock()

    with patch("app.api.v1.dashboard.aioredis.from_url", return_value=mock_redis):
        with client.websocket_connect(f"/api/v1/dashboard/ws?token={token}") as ws:
            msg = ws.receive_text()
            data = json.loads(msg)
            assert data["timestamp"] == "2026-06-06T00:00:00"
            assert data["global"]["users_registered"] == 5


def test_ws_no_initial_snapshot_when_redis_empty(client):
    """Si kpi:latest no existe, no se envía nada al conectar (solo espera updates)."""
    token = _admin_token()

    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)  # Sin snapshot
    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()
    pubsub.get_message = AsyncMock(return_value=None)
    mock_redis.pubsub = MagicMock(return_value=pubsub)
    mock_redis.aclose = AsyncMock()

    with patch("app.api.v1.dashboard.aioredis.from_url", return_value=mock_redis):
        with client.websocket_connect(f"/api/v1/dashboard/ws?token={token}") as ws:
            # No debe haber mensaje inicial. `receive_text()` de Starlette
            # bloquea indefinidamente (queue.get sin timeout) — leemos
            # directamente de la cola interna con timeout para verificar
            # que no llega nada en una ventana razonable.
            import queue
            with pytest.raises(queue.Empty):
                ws._send_queue.get(timeout=1.0)


# ── Reenvío de pubsub ───────────────────────────────────────────────────────


def test_ws_forwards_published_updates(client):
    """Mensajes publicados en kpi:updates se reenvían al cliente."""
    token = _advisor_token()  # advisor también permitido

    update_payload = json.dumps({"timestamp": "2026-06-06T00:01:00", "global": {"users_registered": 7}})

    # get() retorna None al inicio (sin snapshot)
    # pubsub.get_message() retorna un mensaje la primera vez, luego None
    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()
    pubsub.get_message = AsyncMock(side_effect=[
        {"type": "message", "data": update_payload.encode()},  # Primer poll: update
        None,  # Polls siguientes: vacío
        None,
    ])

    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.pubsub = MagicMock(return_value=pubsub)
    mock_redis.aclose = AsyncMock()

    with patch("app.api.v1.dashboard.aioredis.from_url", return_value=mock_redis):
        with client.websocket_connect(f"/api/v1/dashboard/ws?token={token}") as ws:
            msg = ws.receive_text()
            data = json.loads(msg)
            assert data["timestamp"] == "2026-06-06T00:01:00"
            assert data["global"]["users_registered"] == 7


# ── Cleanup ─────────────────────────────────────────────────────────────────


def test_ws_closes_redis_resources_on_disconnect(client):
    """Al desconectar, se cierran pubsub.aclose() y r.aclose()."""
    token = _admin_token()

    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()
    pubsub.get_message = AsyncMock(return_value=None)

    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.pubsub = MagicMock(return_value=pubsub)
    mock_redis.aclose = AsyncMock()

    with patch("app.api.v1.dashboard.aioredis.from_url", return_value=mock_redis):
        with client.websocket_connect(f"/api/v1/dashboard/ws?token={token}") as ws:
            pass  # Salir del context = cliente desconecta

        # El cleanup ocurre en el task async del servidor — corre en background
        # tras cerrar el websocket, así que esperamos brevemente (poll) a que
        # `finally` termine de invocar unsubscribe/aclose antes de afirmar.
        import time
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and pubsub.unsubscribe.call_count == 0:
            time.sleep(0.05)

    # Verificar cleanup
    pubsub.unsubscribe.assert_called_once_with("kpi:updates")
    pubsub.aclose.assert_called_once()
    mock_redis.aclose.assert_called_once()


def test_ws_survives_redis_error_on_connect(client):
    """Si Redis.get falla al inicio, el WS se cierra limpiamente sin crashear."""
    token = _admin_token()

    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(side_effect=Exception("Connection refused"))
    mock_redis.aclose = AsyncMock()

    import queue

    with patch("app.api.v1.dashboard.aioredis.from_url", return_value=mock_redis):
        # El handler entra al `except Exception` y cierra sin crashear el
        # servidor — el cliente puede recibir un cierre, una excepción, o
        # simplemente nada (timeout). `receive_text()` de Starlette bloquea
        # sin límite (queue.get sin timeout), así que leemos la cola interna
        # con timeout para no colgar el test ante cualquiera de esos casos.
        with client.websocket_connect(f"/api/v1/dashboard/ws?token={token}") as ws:
            try:
                ws._send_queue.get(timeout=2.0)
            except queue.Empty:
                pass  # Servidor no envió nada — también es un cierre limpio

    # aclose debe haberse llamado (finally) — esto es lo que importa: el
    # servicio limpia recursos de Redis aunque `r.get` falle al conectar.
    mock_redis.aclose.assert_called_once()
