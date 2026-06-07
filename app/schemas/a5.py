from pydantic import BaseModel, ConfigDict
from typing import Optional, Literal
from uuid import UUID
from datetime import datetime

LeadStatusLiteral = Literal[
    "new", "contacted", "interested", "scheduled",
    "visited", "closed_won", "closed_lost"
]


class LeadCreate(BaseModel):
    property_id: UUID


class LeadUpdate(BaseModel):
    status: Optional[LeadStatusLiteral] = None
    notes: Optional[str] = None


class LeadResponse(BaseModel):
    id: UUID
    user_id: UUID
    user_name: Optional[str] = None
    property_id: UUID
    property_title: Optional[str] = None
    status: str
    notes: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class AppointmentCreate(BaseModel):
    lead_id: Optional[UUID] = None
    property_id: UUID
    scheduled_at: datetime


class AppointmentResponse(BaseModel):
    id: UUID
    lead_id: UUID
    property_id: UUID
    property_title: Optional[str] = None
    user_name: Optional[str] = None
    scheduled_at: datetime
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


VisitDecisionLiteral = Literal["oferta", "ver_otras", "descartar"]


class PostVisitReportUpsert(BaseModel):
    property_id: UUID
    appointment_id: Optional[UUID] = None
    ratings: dict[str, int] = {}
    notes: Optional[str] = None
    decision: Optional[VisitDecisionLiteral] = None
    offer_price: Optional[float] = None
    offer_condition: Optional[str] = None
    offer_deadline: Optional[str] = None
    offer_notes: Optional[str] = None


class PostVisitReportResponse(BaseModel):
    id: UUID
    user_id: UUID
    property_id: UUID
    property_title: Optional[str] = None
    appointment_id: Optional[UUID]
    ratings: dict
    notes: Optional[str]
    decision: Optional[str]
    offer_price: Optional[float]
    offer_condition: Optional[str]
    offer_deadline: Optional[str]
    offer_notes: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


ClosingStepLiteral = Literal["oferta", "negociacion", "documentos", "firma", "confirmado"]
ClosingActionLiteral = Literal[
    "advance", "accept_counter", "counter_offer",
    "toggle_document", "proceed_to_signing", "sign",
]


class ClosingActionRequest(BaseModel):
    action: ClosingActionLiteral
    counter_price: Optional[float] = None
    document_key: Optional[str] = None


class SaleClosingResponse(BaseModel):
    id: UUID
    user_id: UUID
    property_id: UUID
    property_title: Optional[str] = None
    step: str
    offer_price: float
    counter_offer_price: Optional[float]
    agreed_price: Optional[float]
    negotiation_message: Optional[str]
    documents: dict
    legal_summary: Optional[str]
    signed: bool
    signed_at: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class SlotConflictResponse(BaseModel):
    detail: str = "slot_taken"
    available_slots: list[datetime]


class NotificationResponse(BaseModel):
    id: UUID
    user_id: UUID
    title: str
    message: str
    type: str
    read: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotifyManualRequest(BaseModel):
    """Payload para POST /notify (demo manual)."""
    title: str
    message: str
    type: Literal["info", "success", "warning"] = "info"
