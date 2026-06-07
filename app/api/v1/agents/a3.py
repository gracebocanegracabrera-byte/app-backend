from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.core.database import get_db
from app.core.auth import get_current_user
from app.agents.a3.service import agent_a3
from app.models.profile import ChatMessage, UserProfile
from app.models.user import User
from app.schemas.a3 import ChatRequest, ChatResponse, ProfileResponse, ProfileUpdate, MessageResponse

VALID_FIELDS = {"zone", "price_min", "price_max", "property_type", "bedrooms", "area_m2_min", "purpose"}
NUMERIC_FIELDS = {"price_min", "price_max", "bedrooms", "area_m2_min"}


def _coerce_field(field: str, value):
    """Convierte strings a número para campos numéricos; None/''/null → None."""
    if value is None or value == "":
        return None
    if field in NUMERIC_FIELDS:
        try:
            v = float(str(value))
            return int(v) if v == int(v) else v
        except (ValueError, TypeError):
            return value
    return value

router = APIRouter(prefix="/agents/a3", tags=["agent-a3"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    data: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await agent_a3.chat(current_user.id, data.message, db, user_name=current_user.name)
    profile = await agent_a3.get_profile(current_user.id, db)
    completeness = profile.completeness_pct if profile else 0.0
    return ChatResponse(
        response=result["response"],
        profile_completeness=completeness,
        auto_correction=result.get("auto_correction"),
    )


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await agent_a3.get_profile(current_user.id, db)
    if not profile:
        raise HTTPException(404, "Perfil no generado aún")
    return profile


@router.put("/profile", response_model=ProfileResponse)
async def update_profile(
    data: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await agent_a3.get_profile(current_user.id, db)
    if not profile:
        raise HTTPException(404, "Perfil no generado aún")

    updates = data.model_dump(exclude_unset=True)
    # Reemplazar dict completo — SQLAlchemy no trackea mutación in-place de JSONB
    new_prefs = dict(profile.preferences)
    for field, value in updates.items():
        if field in VALID_FIELDS:
            new_prefs[field] = _coerce_field(field, value)
    profile.preferences = new_prefs

    filled = sum(1 for f in VALID_FIELDS if new_prefs.get(f) is not None)
    profile.completeness_pct = round((filled / len(VALID_FIELDS)) * 100, 1)
    await db.commit()
    await db.refresh(profile)
    return profile


@router.get("/history", response_model=list[MessageResponse])
async def get_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.user_id == current_user.id)
        .order_by(ChatMessage.created_at)
    )
    return result.scalars().all()


@router.delete("/history", status_code=204)
async def clear_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        delete(ChatMessage).where(ChatMessage.user_id == current_user.id)
    )
    await db.commit()
