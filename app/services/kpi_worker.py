import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import AsyncSessionLocal
from app.models.user import User
from app.models.profile import UserProfile, ChatMessage
from app.models.property import Property
from app.models.evaluation import Evaluation
from app.models.ranking import Ranking
from app.models.crm import Lead, Appointment, Notification
from app.models.kpi import KpiSnapshot
import redis.asyncio as aioredis
from app.core.config import settings

logger = logging.getLogger(__name__)


class KPIWorker:
    """
    Calcula KPIs y los publica en Redis.

    Persistencia en BD (kpi_snapshots):
    - Solo guarda si el valor es > 0
    - Solo guarda si el valor CAMBIÓ respecto al último snapshot guardado
    → Evita acumulación de ceros y duplicados sin información nueva
    """

    def __init__(self):
        # Cache en memoria: {"global.users_registered": 3.0, ...}
        # Evita consultar BD para comparar el valor anterior
        self._last_saved: dict[str, float] = {}

    def _snapshot_key(self, agent: str, metric: str) -> str:
        return f"{agent}.{metric}"

    def _should_persist(self, agent: str, metric: str, value: float) -> bool:
        """
        Retorna True si vale la pena persistir el snapshot:
        1. Valor debe ser > 0  (ceros no aportan histórico útil)
        2. Valor debe haber CAMBIADO desde el último guardado
        """
        if value <= 0:
            return False
        key = self._snapshot_key(agent, metric)
        last = self._last_saved.get(key)
        return last is None or last != value

    async def calculate_all(self, db: AsyncSession) -> dict:
        now = datetime.now(timezone.utc)

        # ── KPIs Globales ──
        users_count = (await db.execute(select(func.count()).select_from(User))).scalar()
        # Solo "buyer" pasa por perfilamiento/evaluación/ranking/citas — admin y advisor
        # son roles operativos, no clientes. El funnel de conversión debe partir de
        # este conteo, no del total de usuarios (que infla la base y distorsiona %).
        customers_count = (await db.execute(
            select(func.count()).select_from(User).where(User.role == "buyer")
        )).scalar()
        profiles_count = (await db.execute(select(func.count()).select_from(UserProfile))).scalar()
        properties_count = (await db.execute(
            select(func.count()).select_from(Property).where(Property.is_active == True)
        )).scalar()
        evaluations_count = (await db.execute(select(func.count()).select_from(Evaluation))).scalar()
        rankings_count = (await db.execute(select(func.count()).select_from(Ranking))).scalar()
        appointments_count = (await db.execute(select(func.count()).select_from(Appointment))).scalar()

        # ── KPIs A3 ──
        a3_profiles = profiles_count
        a3_interactions = (await db.execute(select(func.count()).select_from(ChatMessage))).scalar()
        a3_avg_completeness_result = await db.execute(select(func.avg(UserProfile.completeness_pct)))
        a3_avg_completeness = round(a3_avg_completeness_result.scalar() or 0, 1)

        # ── KPIs A1 ──
        a1_total = properties_count
        a1_active = properties_count
        last_scraped = (await db.execute(select(func.max(Property.scraped_at)))).scalar()

        # ── KPIs A2 ──
        a2_total = evaluations_count
        a2_green = (await db.execute(
            select(func.count()).select_from(Evaluation).where(Evaluation.legal_status == "green")
        )).scalar()
        a2_yellow = (await db.execute(
            select(func.count()).select_from(Evaluation).where(Evaluation.legal_status == "yellow")
        )).scalar()
        a2_red = (await db.execute(
            select(func.count()).select_from(Evaluation).where(Evaluation.legal_status == "red")
        )).scalar()

        # ── KPIs A4 ──
        a4_rankings = rankings_count
        a4_props_recommended = (await db.execute(select(func.count()).select_from(Ranking))).scalar()

        # ── KPIs A5 ──
        a5_appointments = appointments_count
        a5_leads = (await db.execute(select(func.count()).select_from(Lead))).scalar()
        a5_notifications = (await db.execute(select(func.count()).select_from(Notification))).scalar()

        kpis = {
            "timestamp": now.isoformat(),
            "global": {
                "users_registered": users_count,
                "customers_registered": customers_count,
                "profiles_generated": a3_profiles,
                "properties_analyzed": properties_count,
                "evaluations_done": evaluations_count,
                "rankings_generated": rankings_count,
                "appointments_made": appointments_count,
            },
            "a3": {
                "profiles_created": a3_profiles,
                "avg_interactions": round(a3_interactions / max(a3_profiles, 1), 1),
                "avg_completeness_pct": a3_avg_completeness,
            },
            "a1": {
                "properties_collected": a1_total,
                "active_properties": a1_active,
                "last_updated": last_scraped.isoformat() if last_scraped else None,
            },
            "a2": {
                "evaluations_done": a2_total,
                "green": a2_green,
                "yellow": a2_yellow,
                "red": a2_red,
            },
            "a4": {
                "rankings_generated": a4_rankings,
                "properties_recommended": a4_props_recommended,
            },
            "a5": {
                "appointments_registered": a5_appointments,
                "leads_tracked": a5_leads,
                "communications_sent": a5_notifications,
            },
        }

        # ── Persistencia selectiva ─────────────────────────────────────
        # Solo guarda si valor > 0 Y cambió desde el último guardado.
        # Así kpi_snapshots contiene SOLO cambios reales, sin ruido de ceros.
        saved_count = 0
        for agent, metrics in kpis.items():
            if agent == "timestamp":
                continue
            if not isinstance(metrics, dict):
                continue
            for metric_name, value in metrics.items():
                if not isinstance(value, (int, float)) or value is None:
                    continue
                fval = float(value)
                if self._should_persist(agent, metric_name, fval):
                    db.add(KpiSnapshot(
                        agent=agent,
                        metric_name=metric_name,
                        value=fval,
                    ))
                    key = self._snapshot_key(agent, metric_name)
                    self._last_saved[key] = fval
                    saved_count += 1

        if saved_count > 0:
            await db.commit()
            logger.info(f"KPI: {saved_count} snapshots nuevos persistidos (con cambio y valor > 0)")
        else:
            logger.debug("KPI: sin cambios — no se escribio en BD")

        return kpis

    async def publish_to_redis(self, kpis: dict):
        r = aioredis.from_url(settings.REDIS_URL)
        try:
            payload = json.dumps(kpis)
            await r.set("kpi:latest", payload)
            await r.publish("kpi:updates", payload)
        finally:
            await r.aclose()

    async def run_forever(self, interval: int = 30):
        while True:
            try:
                async with AsyncSessionLocal() as db:
                    kpis = await self.calculate_all(db)
                await self.publish_to_redis(kpis)
            except Exception as e:
                logger.error(f"KPI worker error: {e}")
            await asyncio.sleep(interval)


kpi_worker = KPIWorker()
