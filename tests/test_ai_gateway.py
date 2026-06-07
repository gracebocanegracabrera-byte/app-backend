import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from uuid import uuid4
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from app.core.database import Base
from app.core.ai_gateway import ai_complete, ai_json
from app.agents.a3.service import agent_a3

# Hacer que JSONB funcione con SQLite en tests
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture
def mock_create():
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


@pytest.mark.asyncio
async def test_ai_complete_returns_text(mock_create):
    result = await ai_complete(
        model="test-model",
        messages=[{"role": "user", "content": "Hola"}],
        system="Eres un asistente",
    )
    assert result == "respuesta mock"
    mock_create.assert_awaited_once()


@pytest.mark.asyncio
async def test_ai_complete_without_system(mock_create):
    result = await ai_complete(
        model="test-model",
        messages=[{"role": "user", "content": "Hola"}],
    )
    assert result == "respuesta mock"


@pytest.mark.asyncio
async def test_ai_complete_fallback_on_429(mock_create):
    from openai import APIStatusError

    fallback_response = MagicMock(
        choices=[MagicMock(message=MagicMock(content="fallback ok"))]
    )
    mock_create.side_effect = [
        APIStatusError(
            message="Rate limit",
            response=MagicMock(status_code=429),
            body=None,
        ),
        fallback_response,
    ]
    mock_create.return_value = fallback_response

    result = await ai_complete(
        model="primary-model",
        messages=[{"role": "user", "content": "Hola"}],
    )
    assert result == "fallback ok"
    assert mock_create.await_count == 2


@pytest.mark.asyncio
async def test_ai_json_returns_dict(mock_create):
    import json

    data = {"zone": "Victor Larco", "price_min": 200000}
    mock_create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(data)))]
    )

    result = await ai_json(
        model="test-model",
        messages=[{"role": "user", "content": "Extrae perfil"}],
    )
    assert result == data


@pytest.mark.asyncio
async def test_ai_json_cleans_markdown_fences(mock_create):
    mock_create.return_value = MagicMock(
        choices=[
            MagicMock(message=MagicMock(content='```json\n{"zone": "Huanchaco"}\n```'))
        ]
    )

    result = await ai_json(
        model="test-model",
        messages=[{"role": "user", "content": "Extrae"}],
    )
    assert result == {"zone": "Huanchaco"}


@pytest.mark.asyncio
async def test_ai_json_returns_empty_on_all_failures(mock_create):
    mock_create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="not json at all"))]
    )

    result = await ai_json(
        model="test-model",
        messages=[{"role": "user", "content": "Extrae"}],
    )
    assert result == {}


@pytest.mark.asyncio
async def test_agent_a3_chat_saves_and_returns(mock_create, db_session):
    user_id = uuid4()
    result = await agent_a3.chat(user_id, "Hola, busco un departamento", db_session)
    assert result == "respuesta mock"

    history = await agent_a3.get_history(user_id, db_session)
    assert len(history) == 2
    roles = {m["role"] for m in history}
    assert roles == {"user", "assistant"}


@pytest.mark.asyncio
async def test_agent_a3_chat_triggers_profile_after_4(mock_create, db_session):
    import json

    user_id = uuid4()
    profile_data = {
        "zone": "Victor Larco",
        "price_min": 200000,
        "price_max": 500000,
        "property_type": "departamento",
        "bedrooms": 3,
        "area_m2_min": 80,
        "purpose": "compra",
    }
    mock_create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(profile_data)))]
    )

    for msg in ["Hola", "Busco en Victor Larco", "Presupuesto 200-500k", "3 dorm"]:
        await agent_a3.chat(user_id, msg, db_session)

    profile = await agent_a3.get_profile(user_id, db_session)
    assert profile is not None
    assert profile.preferences["zone"] == "Victor Larco"
    assert profile.completeness_pct == 100.0


@pytest.mark.asyncio
async def test_agent_a3_get_history_limit(mock_create, db_session):
    user_id = uuid4()
    from app.models.profile import ChatMessage
    from datetime import datetime, timezone, timedelta

    # Timestamps explícitos: 1 segundo de diferencia para orden determinista en SQLite
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(25):
        db_session.add(ChatMessage(
            user_id=user_id,
            role="user",
            content=f"msg {i}",
            created_at=base_time + timedelta(seconds=i),
        ))
    await db_session.commit()

    history = await agent_a3.get_history(user_id, db_session)
    # Debe devolver los 20 más recientes (msg 5..24), en orden cronológico
    assert len(history) == 20
    assert history[0]["content"] == "msg 5"    # más antiguo de los últimos 20
    assert history[-1]["content"] == "msg 24"  # más reciente


@pytest.mark.asyncio
async def test_agent_a3_get_profile_returns_none(mock_create, db_session):
    profile = await agent_a3.get_profile(uuid4(), db_session)
    assert profile is None
