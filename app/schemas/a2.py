from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from datetime import datetime


class EvaluationResponse(BaseModel):
    id: UUID
    property_id: UUID
    ref_price: Optional[float]
    legal_status: str
    risk_level: str
    report: dict
    created_at: datetime

    model_config = {"from_attributes": True}
