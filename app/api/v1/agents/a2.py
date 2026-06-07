from fastapi import APIRouter, Depends, HTTPException
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.auth import get_current_user
from app.agents.a2.service import agent_a2
from app.models.evaluation import Evaluation
from app.schemas.a2 import EvaluationResponse

router = APIRouter(prefix="/agents/a2", tags=["agent-a2"])


@router.post("/evaluate/{property_id}", response_model=EvaluationResponse)
async def evaluate_property(
    property_id: UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        evaluation = await agent_a2.evaluate_property(property_id, current_user.id, db)
        return evaluation
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/report/{evaluation_id}", response_model=EvaluationResponse)
async def get_report(
    evaluation_id: UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Evaluation).where(
            Evaluation.id == evaluation_id,
            Evaluation.user_id == current_user.id
        )
    )
    evaluation = result.scalar_one_or_none()
    if not evaluation:
        raise HTTPException(404, "Evaluación no encontrada")
    return evaluation


@router.get("/evaluations", response_model=list[EvaluationResponse])
async def list_evaluations(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Evaluation).where(Evaluation.user_id == current_user.id)
        .order_by(Evaluation.created_at.desc())
    )
    return result.scalars().all()
