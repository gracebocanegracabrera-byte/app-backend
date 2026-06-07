from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from uuid import UUID
from datetime import datetime


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)


class ChatResponse(BaseModel):
    response: str
    profile_completeness: float = 0.0
    auto_correction: Optional[str] = None  # Mensaje si A3 auto-corrigió purpose por inconsistencia de precios


class ProfileResponse(BaseModel):
    id: UUID
    preferences: dict
    completeness_pct: float
    created_at: datetime
    updated_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)

class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra='allow')


class MessageResponse(BaseModel):
    id: UUID
    role: str
    content: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
