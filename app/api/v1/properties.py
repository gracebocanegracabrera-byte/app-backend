from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.auth import get_current_user
from app.agents.a1.service import agent_a1
from app.models.property import Property
from app.schemas.a1 import PropertyOut

router = APIRouter(prefix="/properties", tags=["properties"])


class PropertiesResponse(BaseModel):
    items: list[PropertyOut]
    total: int
    page: int
    pages: int
    relaxed_filters: list[str] = []  # filtros relajados por la cascade (e.g. ["zone", "price"])
    suggestion: Optional[str] = None  # mensaje explicativo de la aproximación


@router.get("", response_model=PropertiesResponse)
async def list_properties(
    district: Optional[str] = Query(None),
    price_min: Optional[float] = Query(None),
    price_max: Optional[float] = Query(None),
    property_type: Optional[str] = Query(None),
    listing_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=50),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, total, relaxed, suggestion = await agent_a1.get_filtered_properties(
        db,
        user_id=current_user.id,
        district=district,
        price_min=price_min,
        price_max=price_max,
        property_type=property_type,
        listing_type=listing_type,
        page=page,
        limit=limit,
    )
    pages = max(1, -(-total // limit))
    return PropertiesResponse(
        items=items,
        total=total,
        page=page,
        pages=pages,
        relaxed_filters=relaxed,
        suggestion=suggestion,
    )


@router.get("/{property_id}", response_model=PropertyOut)
async def get_property(
    property_id: UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Property).where(
            Property.id == property_id,
            Property.is_active == True,
        )
    )
    prop = result.scalar_one_or_none()
    if not prop:
        raise HTTPException(404, "Propiedad no encontrada")
    return prop
