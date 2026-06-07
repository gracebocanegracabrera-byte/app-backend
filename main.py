import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import router as api_v1_router

# ── Logging setup ──────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    handlers=[
        logging.FileHandler("logs/communications.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    from app.core.database import AsyncSessionLocal
    from app.agents.a1.service import agent_a1
    from app.services.kpi_worker import kpi_worker

    async def run_scraping_bg():
        try:
            async with AsyncSessionLocal() as db:
                imported = await agent_a1.run_scraping(db)
                if imported:
                    print(f"[startup] Scraping: {imported} propiedades nuevas importadas")
                else:
                    print("[startup] Scraping: sin propiedades nuevas (ya actualizadas)")
        except Exception as e:
            print(f"[startup] Scraping falló: {e}")

    # Guard: en tests (pytest instancia TestClient/ASGITransport en decenas de
    # archivos) NO lanzar tareas perpetuas contra recursos reales (httpx a
    # urbania.pe + loop Redis) — eso cuelga la suite de forma reproducible.
    is_testing = bool(os.getenv("PYTEST_CURRENT_TEST")) or os.getenv("ENV") == "test"
    if not is_testing:
        # Lanzar en background para no bloquear el arranque del servidor.
        # Guardamos referencia en app.state — evita GC prematuro de la task
        # (asyncio docs: "save a reference to the result of create_task").
        app.state.scraping_task = asyncio.create_task(run_scraping_bg())
        # KPI Worker — calcula y publica KPIs cada 30s
        app.state.kpi_task = asyncio.create_task(kpi_worker.run_forever(interval=30))
    yield


app = FastAPI(
    title="MVP Inmobiliario IA",
    description="Plataforma inmobiliaria con 5 agentes IA",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4200",
        os.getenv("FRONTEND_URL", "http://localhost:4200"),
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"message": "MVP Inmobiliario IA API"}
