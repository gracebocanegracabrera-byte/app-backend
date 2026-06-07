from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.core.database import get_db
from app.core.auth import get_current_user, require_role
from app.agents.a1.service import agent_a1
from app.models.property import Property
from app.schemas.a1 import ScrapeResponse, A1StatusResponse

router = APIRouter(prefix="/agents/a1", tags=["agent-a1"])


@router.post("/scrape", response_model=ScrapeResponse)
async def run_scrape(
    current_user=Depends(require_role("admin", "advisor")),
    db: AsyncSession = Depends(get_db),
):
    imported = await agent_a1.run_scraping(db)
    total_result = await db.execute(
        select(func.count()).select_from(Property).where(Property.is_active == True)
    )
    total = total_result.scalar()
    return ScrapeResponse(
        imported=imported,
        total=total,
        message=f"Importadas {imported} propiedades nuevas. Total activas: {total}",
    )


@router.get("/status", response_model=A1StatusResponse)
async def get_status(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    count_result = await db.execute(
        select(func.count())
        .select_from(Property)
        .where(Property.is_active == True)
    )
    last_result = await db.execute(select(func.max(Property.scraped_at)))
    sources_result = await db.execute(select(Property.source_name).distinct())
    return A1StatusResponse(
        active_properties=count_result.scalar() or 0,
        last_updated=last_result.scalar(),
        sources=[r[0] for r in sources_result.fetchall() if r[0]],
    )
