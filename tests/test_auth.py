import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from app.core.database import Base, get_db
from app.core.config import settings

from main import app

engine = None
TestingSessionLocal = None


async def cleanup_db():
    global engine, TestingSessionLocal
    async with TestingSessionLocal() as session:
        for table in reversed(Base.metadata.sorted_tables):
            await session.execute(table.delete())
        await session.commit()


@pytest.fixture(autouse=True)
async def setup_db():
    global engine, TestingSessionLocal
    if engine is None:
        engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
        TestingSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    await cleanup_db()
    yield


async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session


@pytest.fixture
async def client():
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


class TestRegister:
    async def test_register_creates_user_and_returns_tokens(self, client):
        response = await client.post("/api/v1/auth/register", json={
            "email": "new@example.com",
            "name": "New User",
            "password": "securepass1",
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    async def test_register_rejects_duplicate_email(self, client):
        await client.post("/api/v1/auth/register", json={
            "email": "dup@example.com",
            "name": "First",
            "password": "pass12345",
        })
        response = await client.post("/api/v1/auth/register", json={
            "email": "dup@example.com",
            "name": "Second",
            "password": "pass12345",
        })
        assert response.status_code == 400
        assert "ya registrado" in response.json()["detail"]

    async def test_register_password_not_returned(self, client):
        response = await client.post("/api/v1/auth/register", json={
            "email": "safe@example.com",
            "name": "Safe User",
            "password": "mysecret123",
        })
        body = response.text.lower()
        assert "password" not in body or "mysecret123" not in body


class TestLogin:
    async def test_login_valid_credentials(self, client):
        await client.post("/api/v1/auth/register", json={
            "email": "login@example.com",
            "name": "Login User",
            "password": "validpass1",
        })
        response = await client.post("/api/v1/auth/login", json={
            "email": "login@example.com",
            "password": "validpass1",
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_login_invalid_password(self, client):
        await client.post("/api/v1/auth/register", json={
            "email": "badpass@example.com",
            "name": "Bad Password",
            "password": "correctpass",
        })
        response = await client.post("/api/v1/auth/login", json={
            "email": "badpass@example.com",
            "password": "wrongpass1",
        })
        assert response.status_code == 401
        assert "inválidas" in response.json()["detail"]

    async def test_login_nonexistent_user(self, client):
        response = await client.post("/api/v1/auth/login", json={
            "email": "nobody@example.com",
            "password": "somepass1",
        })
        assert response.status_code == 401


class TestRefresh:
    async def test_refresh_valid_token(self, client):
        reg = await client.post("/api/v1/auth/register", json={
            "email": "refresh@example.com",
            "name": "Refresh User",
            "password": "refreshpass",
        })
        refresh_token = reg.json()["refresh_token"]

        response = await client.post("/api/v1/auth/refresh", json={
            "refresh_token": refresh_token,
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_refresh_invalid_token(self, client):
        response = await client.post("/api/v1/auth/refresh", json={
            "refresh_token": "invalid.token.here",
        })
        assert response.status_code == 401


class TestMe:
    async def test_me_returns_user_data(self, client):
        reg = await client.post("/api/v1/auth/register", json={
            "email": "me@example.com",
            "name": "Me User",
            "password": "mepassword",
        })
        token = reg.json()["access_token"]

        response = await client.get("/api/v1/auth/me", headers={
            "Authorization": f"Bearer {token}",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "me@example.com"
        assert data["name"] == "Me User"
        assert data["role"] == "buyer"
        assert "id" in data

    async def test_me_without_token_returns_401(self, client):
        response = await client.get("/api/v1/auth/me")
        assert response.status_code in (401, 403)

    async def test_me_with_bad_token_returns_401(self, client):
        response = await client.get("/api/v1/auth/me", headers={
            "Authorization": "Bearer badtokenhere",
        })
        assert response.status_code == 401
