import uuid
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.core.database import Base
from app.models.property import Property
from app.agents.a1.service import agent_a1


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
async def seed_filter_props(db_session):
    """4 propiedades para testear los tiers de la cascade."""
    props = [
        Property(
            id=uuid.uuid4(), title="Depa Golf Victor Larco", price=500000, area_m2=120,
            zone="Zona Golf", district="Victor Larco", property_type="departamento",
            bedrooms=3, listing_type="sale", is_active=True, status="available",
            source_url="test://golf", source_name="test",
        ),
        Property(
            id=uuid.uuid4(), title="Depa Centro Trujillo", price=200000, area_m2=70,
            zone="Trujillo Centro", district="Trujillo", property_type="departamento",
            bedrooms=2, listing_type="sale", is_active=True, status="available",
            source_url="test://centro", source_name="test",
        ),
        Property(
            id=uuid.uuid4(), title="Casa Huanchaco", price=350000, area_m2=150,
            zone="Zona Playera", district="Huanchaco", property_type="casa",
            bedrooms=3, listing_type="sale", is_active=True, status="available",
            source_url="test://huanchaco", source_name="test",
        ),
        Property(
            id=uuid.uuid4(), title="Depa Alquiler Centro", price=1500, area_m2=80,
            zone="Trujillo Centro", district="Trujillo", property_type="departamento",
            bedrooms=2, listing_type="rent", is_active=True, status="available",
            source_url="test://alquiler", source_name="test",
        ),
    ]
    for p in props:
        db_session.add(p)
    await db_session.commit()
    return props


# ─── Tier 0: Match exacto ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cascade_exact_match(db_session, seed_filter_props):
    items, total, relaxed, suggestion = await agent_a1.get_filtered_properties(
        db_session, district="Victor Larco", property_type="departamento", limit=10,
    )
    assert total == 1
    assert items[0].district == "Victor Larco"
    assert items[0].property_type == "departamento"
    assert relaxed == []
    assert suggestion is None


@pytest.mark.asyncio
async def test_cascade_exact_match_by_district_only(db_session, seed_filter_props):
    items, total, relaxed, suggestion = await agent_a1.get_filtered_properties(
        db_session, district="Trujillo", limit=10,
    )
    assert total == 2
    assert relaxed == []
    assert suggestion is None


# ─── Tier 1: relaja zona ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cascade_relaxes_zone(db_session, seed_filter_props):
    items, total, relaxed, suggestion = await agent_a1.get_filtered_properties(
        db_session, district="Lima Miraflores", property_type="departamento", limit=10,
    )
    assert total == 3
    assert "zone" in relaxed
    assert suggestion is not None
    assert "zona" in suggestion.lower()


# ─── Tier 2-3: relaja tipo + precio ────────────────────────────────────────


@pytest.mark.asyncio
async def test_cascade_relaxes_price(db_session, seed_filter_props):
    # listing_type="sale" excluye el rental barato; price_max=50000 es
    # muy bajo para las ventas (mín S/78,000) → fuerza relajar price
    items, total, relaxed, suggestion = await agent_a1.get_filtered_properties(
        db_session, listing_type="sale", district="Trujillo",
        property_type="departamento", price_max=50000, limit=10,
    )
    assert total >= 1
    assert "price" in relaxed
    # La mejor coincidencia (Centro: type=dpto + district=Trujillo + price más cercano)
    # debe ser la primera
    assert items[0].district == "Trujillo"
    assert items[0].property_type == "departamento"


@pytest.mark.asyncio
async def test_cascade_relaxes_type_and_price(db_session, seed_filter_props):
    items, total, relaxed, suggestion = await agent_a1.get_filtered_properties(
        db_session, listing_type="sale", district="Trujillo",
        property_type="penthouse", price_max=100000, limit=10,
    )
    assert total >= 1
    assert "property_type" in relaxed
    assert "price" in relaxed


# ─── Tier 4: relaja listing_type (alquiler → venta) ───────────────────────


@pytest.mark.asyncio
async def test_cascade_rent_falls_back_to_sale(db_session, seed_filter_props):
    # Borrar la propiedad de alquiler
    await db_session.execute(
        Property.__table__.delete().where(Property.source_url == "test://alquiler")
    )
    await db_session.commit()

    items, total, relaxed, suggestion = await agent_a1.get_filtered_properties(
        db_session, listing_type="rent", limit=10,
    )
    assert total == 3
    assert all(p.listing_type == "sale" for p in items)
    assert "listing_type" in relaxed
    assert "alquiler" in suggestion.lower()
    assert "ventas" in suggestion.lower()


# ─── Rent con match exacto ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cascade_rent_exact_match(db_session, seed_filter_props):
    items, total, relaxed, suggestion = await agent_a1.get_filtered_properties(
        db_session, listing_type="rent", district="Trujillo", limit=10,
    )
    assert total == 1
    assert items[0].listing_type == "rent"
    assert relaxed == []
    assert suggestion is None


# ─── BD vacía ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cascade_empty_db(db_session):
    items, total, relaxed, suggestion = await agent_a1.get_filtered_properties(
        db_session, district="Trujillo", limit=10,
    )
    assert total == 0
    assert items == []
    assert relaxed == []
    assert suggestion is None


# ─── Orden por similitud (tier aproximado) ────────────────────────────────


@pytest.mark.asyncio
async def test_similarity_orders_by_type_match(db_session, seed_filter_props):
    # Perfil: "departamento en Trujillo" → debe preferir departamento sobre casa
    items, total, relaxed, _ = await agent_a1.get_filtered_properties(
        db_session, district="Lima", property_type="departamento", limit=10,
    )
    # Las 3 primeras deben ser "departamento" (tipo coincide)
    types = [p.property_type for p in items]
    assert "departamento" in types
    # El primer item debe ser departamento, no casa
    assert items[0].property_type == "departamento"


@pytest.mark.asyncio
async def test_similarity_orders_by_price_closeness(db_session, seed_filter_props):
    # price_max=180000 → debe preferir Depa Centro (200k) sobre Casa Huanchaco (350k)
    items, total, relaxed, _ = await agent_a1.get_filtered_properties(
        db_session, district="Lima", property_type="departamento",
        price_max=180000, limit=10,
    )
    assert total >= 1
    # La primera propiedad con tipo+precio más cercano al perfil debe ser Depa Centro
    assert items[0].district == "Trujillo"


# ─── Paginación en tier aproximado ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_pagination_in_approximate_tier(db_session, seed_filter_props):
    items, total, relaxed, _ = await agent_a1.get_filtered_properties(
        db_session, listing_type="rent", page=1, limit=2,
    )
    assert total >= 1
    assert len(items) == min(2, total)


@pytest.mark.asyncio
async def test_pagination_page_2(db_session, seed_filter_props):
    items, total, _, _ = await agent_a1.get_filtered_properties(
        db_session, district="Lima", property_type="departamento", page=2, limit=2,
    )
    assert total >= 3
    assert len(items) == 1  # 3 items, page 2 of 2-per-page = 1 item


# ─── Filtros inválidos (zona no string, type no string) ────────────────────


@pytest.mark.asyncio
async def test_cascade_with_only_listing_type(db_session, seed_filter_props):
    items, total, relaxed, suggestion = await agent_a1.get_filtered_properties(
        db_session, listing_type="sale", limit=10,
    )
    assert total == 3
    assert all(p.listing_type == "sale" for p in items)
    assert relaxed == []
    assert suggestion is None


# ─── Excluir sold ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sold_properties_excluded(db_session, seed_filter_props):
    # Marcar una como sold
    sold_prop = seed_filter_props[0]
    sold_prop.status = "sold"
    await db_session.commit()

    items, total, _, _ = await agent_a1.get_filtered_properties(
        db_session, listing_type="sale", limit=10,
    )
    assert all(p.status != "sold" for p in items)
    assert total == 2  # 3 sales - 1 sold


# ─── Cascade con perfil A3 (purpose) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_cascade_with_profile_purpose_alquiler(db_session, seed_filter_props):
    from app.models.profile import UserProfile
    from app.models.user import User
    from app.core.auth import hash_password

    user = User(
        id=uuid.uuid4(),
        email="profile_test@test.com",
        name="Profile Test",
        hashed_password=hash_password("secret123"),
        role="buyer",
    )
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(
        id=uuid.uuid4(),
        user_id=user.id,
        preferences={
            "purpose": "alquiler",
            "zone": "Lima",            # no existe
            "property_type": "penthouse",  # no existe
            "price_max": 50000,
        },
    )
    db_session.add(profile)
    await db_session.commit()

    items, total, relaxed, suggestion = await agent_a1.get_filtered_properties(
        db_session, user_id=user.id, limit=10,
    )
    # El rental existente (S/1500, Trujillo, departamento) cae en tier 2
    # (relax zone + property_type, keep price<=50000 + rent)
    assert total >= 1
    assert "zone" in relaxed
    assert "property_type" in relaxed
    # El primer resultado debe ser el rental existente (única prop rent)
    assert items[0].listing_type == "rent"


@pytest.mark.asyncio
async def test_cascade_profile_purpose_alquiler_falls_to_sale(db_session, seed_filter_props):
    """Si el perfil pide alquiler pero el rental no matchea ni relajando
    zone+type+price, debe caer a tier 4 (ventas)."""
    from app.models.profile import UserProfile
    from app.models.user import User
    from app.core.auth import hash_password

    # Borrar la propiedad de alquiler
    await db_session.execute(
        Property.__table__.delete().where(Property.source_url == "test://alquiler")
    )
    await db_session.commit()

    user = User(
        id=uuid.uuid4(),
        email="profile2@test.com",
        name="Profile Test 2",
        hashed_password=hash_password("secret123"),
        role="buyer",
    )
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(
        id=uuid.uuid4(),
        user_id=user.id,
        preferences={"purpose": "alquiler", "zone": "Lima"},
    )
    db_session.add(profile)
    await db_session.commit()

    items, total, relaxed, suggestion = await agent_a1.get_filtered_properties(
        db_session, user_id=user.id, limit=10,
    )
    assert total >= 1
    assert "listing_type" in relaxed
    assert all(p.listing_type == "sale" for p in items)


# ─── price_min es referencial (NO filtra) ─────────────────────────────────


@pytest.mark.asyncio
async def test_cascade_does_not_filter_below_price_min(db_session, seed_filter_props):
    """price_min=450000 con rental a S/1500 → el rental DEBE aparecer
    (gran oferta: usuario tiene dinero de sobra)."""
    items, total, _, _ = await agent_a1.get_filtered_properties(
        db_session, listing_type="rent", price_min=450000, price_max=7500000,
    )
    # El rental (S/1500) está muy por debajo de price_min pero en budget max
    assert total >= 1
    assert any(p.source_url == "test://alquiler" for p in items)


@pytest.mark.asyncio
async def test_cascade_filters_above_price_max(db_session, seed_filter_props):
    """price_max=100000 SÍ filtra: ningún price > 100k debe aparecer."""
    items, total, _, _ = await agent_a1.get_filtered_properties(
        db_session, listing_type="sale", price_max=100000,
    )
    assert all(p.price <= 100000 for p in items)
    # Golf (500k), Centro (200k), Huanchaco (350k) NO deben estar
    assert all(p.source_url != "test://golf" for p in items)
    assert all(p.source_url != "test://centro" for p in items)
    assert all(p.source_url != "test://huanchaco" for p in items)


@pytest.mark.asyncio
async def test_score_prefers_prices_below_price_min(db_session, seed_filter_props):
    """Propiedades con price < price_min rankean primero (gran oferta).
    La Esperanza (S/78k) < price_min=1M → debe ser la primera."""
    items, total, _, _ = await agent_a1.get_filtered_properties(
        db_session, district="Trujillo", property_type="departamento",
        price_min=1_000_000, price_max=10_000_000,
    )
    assert total >= 1
    # El primer resultado debe tener el menor price (más cercano a "oferta")
    assert items[0].price == min(p.price for p in items)


# ─── Caso exacto reportado por el usuario ──────────────────────────────────


@pytest.mark.asyncio
async def test_user_profile_returns_results(db_session, seed_filter_props):
    """Replica del caso reportado: San Isidro + alquiler + casa + 450k-7.5M.
    Debe devolver resultados (no 0) y elegir un tier con buen top-score."""
    items, total, relaxed, suggestion = await agent_a1.get_filtered_properties(
        db_session,
        district="San Isidro",
        property_type="casa",
        price_min=450000,
        price_max=7500000,
        listing_type="rent",
    )
    assert total > 0
    # San Isidro no existe → al menos 'zone' debe estar relajado
    assert "zone" in relaxed
    # listing_type puede o no estar relajado según best-score
    # (depende de cuál tier tiene mejor top-score)
    assert suggestion is not None

