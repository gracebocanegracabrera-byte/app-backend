import pytest
from uuid import uuid4
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.core.database import Base, get_db
from app.models.property import Property
from main import app


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
        json={
            "email": "buyer@a2test.com",
            "name": "Buyer A2",
            "password": "secret123",
            "role": "buyer",
        },
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.fixture
async def test_property(db_session):
    prop = Property(
        title="Departamento en Victor Larco",
        price=350000,
        area_m2=100,
        district="victor larco",
        zone="Zona Golf",
        property_type="departamento",
        bedrooms=3,
        source_name="urbania",
        source_url="https://urbania.pe/test/123",
        is_active=True,
    )
    db_session.add(prop)
    await db_session.commit()
    await db_session.refresh(prop)
    return prop


class TestA2ServiceAPI:

    @pytest.mark.asyncio
    async def test_evaluate_requires_auth(self, client):
        resp = await client.post(f"/api/v1/agents/a2/evaluate/{uuid4()}")
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_report_requires_auth(self, client):
        resp = await client.get(f"/api/v1/agents/a2/report/{uuid4()}")
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_evaluations_requires_auth(self, client):
        resp = await client.get("/api/v1/agents/a2/evaluations")
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_evaluate_nonexistent_property(self, client, buyer_token):
        fake_id = uuid4()
        resp = await client.post(
            f"/api/v1/agents/a2/evaluate/{fake_id}",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_evaluate_creates_evaluation(self, client, buyer_token, test_property):
        resp = await client.post(
            f"/api/v1/agents/a2/evaluate/{test_property.id}",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["property_id"] == str(test_property.id)
        assert data["legal_status"] in ("green", "yellow", "red")
        assert data["risk_level"] in ("low", "medium", "high")
        assert "report" in data
        assert "price_analysis" in data["report"]
        assert "legal_analysis" in data["report"]
        assert "summary" in data["report"]
        assert data["ref_price"] is not None

    @pytest.mark.asyncio
    async def test_evaluate_dedup_returns_existing(self, client, buyer_token, test_property):
        resp1 = await client.post(
            f"/api/v1/agents/a2/evaluate/{test_property.id}",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp1.status_code == 200
        eval_id_1 = resp1.json()["id"]

        resp2 = await client.post(
            f"/api/v1/agents/a2/evaluate/{test_property.id}",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["id"] == eval_id_1

    @pytest.mark.asyncio
    async def test_get_report_returns_evaluation(self, client, buyer_token, test_property):
        eval_resp = await client.post(
            f"/api/v1/agents/a2/evaluate/{test_property.id}",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        eval_id = eval_resp.json()["id"]

        resp = await client.get(
            f"/api/v1/agents/a2/report/{eval_id}",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == eval_id

    @pytest.mark.asyncio
    async def test_get_report_404_for_other_user(self, client, buyer_token, test_property):
        eval_resp = await client.post(
            f"/api/v1/agents/a2/evaluate/{test_property.id}",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        eval_id = eval_resp.json()["id"]

        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "other@a2test.com",
                "name": "Other",
                "password": "secret123",
                "role": "buyer",
            },
        )
        other_token = resp.json()["access_token"]

        resp2 = await client.get(
            f"/api/v1/agents/a2/report/{eval_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert resp2.status_code == 404

    @pytest.mark.asyncio
    async def test_list_evaluations(self, client, buyer_token, test_property):
        await client.post(
            f"/api/v1/agents/a2/evaluate/{test_property.id}",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        resp = await client.get(
            "/api/v1/agents/a2/evaluations",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["property_id"] == str(test_property.id)

    @pytest.mark.asyncio
    async def test_evaluation_contains_price_analysis(self, client, buyer_token, test_property):
        resp = await client.post(
            f"/api/v1/agents/a2/evaluate/{test_property.id}",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        data = resp.json()
        report = data["report"]
        price = report["price_analysis"]
        assert "ref_price_per_m2" in price
        assert "ref_total_price" in price
        assert "verdict" in price
        assert "price_diff_pct" in price

    @pytest.mark.asyncio
    async def test_evaluation_contains_legal_analysis(self, client, buyer_token, test_property):
        resp = await client.post(
            f"/api/v1/agents/a2/evaluate/{test_property.id}",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        data = resp.json()
        report = data["report"]
        legal = report["legal_analysis"]
        assert "status" in legal
        assert "score" in legal
        assert "risks" in legal
        assert "disclaimer" in legal
        assert "SUNARP" in legal["disclaimer"]

    @pytest.mark.asyncio
    async def test_evaluation_summary_format(self, client, buyer_token, test_property):
        resp = await client.post(
            f"/api/v1/agents/a2/evaluate/{test_property.id}",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        data = resp.json()
        summary = data["report"]["summary"]
        assert isinstance(summary, str)
        assert len(summary) > 0
