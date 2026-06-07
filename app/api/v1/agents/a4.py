from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.auth import get_current_user
from app.agents.a4.service import agent_a4
from app.schemas.a4 import RankingItem

router = APIRouter(prefix="/agents/a4", tags=["agent-a4"])


@router.get("/ranking", response_model=list[RankingItem])
async def get_ranking(
    limit: int = Query(20, ge=1, le=50),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await agent_a4.generate_ranking(current_user.id, db, limit)


@router.get("/recommendations", response_model=list[RankingItem])
async def get_recommendations(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await agent_a4.generate_ranking(current_user.id, db, limit=5)
