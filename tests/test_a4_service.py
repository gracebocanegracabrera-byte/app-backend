"""Regression test: agent_a4.generate_ranking debe consumir el tuple de 4
elementos que retorna agent_a1.get_filtered_properties (items, total, relaxed,
suggestion) — un caller desactualizado (3-unpack) rompía /agents/a4/ranking
con "too many values to unpack" (descubierto al correr scripts/seed_all.py)."""

import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.core.database import Base
from app.models.user import User
from app.models.property import Property
from app.agents.a4.service import agent_a4


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


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
async def buyer_and_property(db_session):
    user = User(
        id=uuid4(),
        email=f"ranking-{uuid4()}@test.com",
        name="Buyer Ranking",
        hashed_password="x",
        role="buyer",
        legal_disclaimer_accepted=True,
        privacy_policy_accepted=True,
        data_processing_consent=True,
    )
    prop = Property(
        title="Depto Ranking Test",
        price=300000,
        area_m2=80,
        district="trujillo",
        zone="Centro",
        property_type="departamento",
        bedrooms=2,
        source_name="urbania",
        source_url=f"https://urbania.pe/test/{uuid4()}",
        is_active=True,
    )
    db_session.add_all([user, prop])
    await db_session.commit()
    await db_session.refresh(user)
    await db_session.refresh(prop)
    return user, prop


@pytest.mark.asyncio
async def test_generate_ranking_unpacks_4_tuple_from_get_filtered_properties(db_session, buyer_and_property):
    """get_filtered_properties retorna (items, total, relaxed, suggestion) — 4 valores.
    Si generate_ranking vuelve a un unpack de 3, esto debe fallar con ValueError."""
    user, prop = buyer_and_property

    with patch(
        "app.agents.a4.service.agent_a1.get_filtered_properties",
        new=AsyncMock(return_value=([prop], 1, [], None)),
    ):
        ranking = await agent_a4.generate_ranking(user.id, db_session, limit=20)

    assert isinstance(ranking, list)
    assert len(ranking) == 1
    assert ranking[0]["property"]["id"] == str(prop.id)
    assert "score" in ranking[0]
    assert "tag" in ranking[0]
