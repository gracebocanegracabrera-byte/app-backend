from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint, Enum, Boolean, Integer, Float, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
import enum
from app.core.database import Base


class LeadStatus(str, enum.Enum):
    new = "new"
    contacted = "contacted"
    interested = "interested"
    scheduled = "scheduled"
    visited = "visited"
    closed_won = "closed_won"
    closed_lost = "closed_lost"


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("user_id", "property_id", name="uq_user_property_lead"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    property_id = Column(UUID(as_uuid=True), ForeignKey("properties.id"), nullable=False)
    status = Column(Enum(LeadStatus), default=LeadStatus.new, nullable=False)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class AppointmentStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    cancelled = "cancelled"
    completed = "completed"


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint("property_id", "scheduled_at", name="uq_property_slot"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=False)
    property_id = Column(UUID(as_uuid=True), ForeignKey("properties.id"), nullable=False)
    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(Enum(AppointmentStatus), default=AppointmentStatus.pending, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class VisitDecision(str, enum.Enum):
    oferta = "oferta"
    ver_otras = "ver_otras"
    descartar = "descartar"


class PostVisitReport(Base):
    __tablename__ = "post_visit_reports"
    __table_args__ = (
        UniqueConstraint("user_id", "property_id", name="uq_user_property_report"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    property_id = Column(UUID(as_uuid=True), ForeignKey("properties.id"), nullable=False)
    appointment_id = Column(UUID(as_uuid=True), ForeignKey("appointments.id"), nullable=True)
    ratings = Column(JSON, nullable=False, default=dict)   # { ubicacion, estado, precio, entorno }
    notes = Column(String, nullable=True)
    decision = Column(Enum(VisitDecision), nullable=True)
    offer_price = Column(Float, nullable=True)
    offer_condition = Column(String, nullable=True)
    offer_deadline = Column(String, nullable=True)
    offer_notes = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ClosingStep(str, enum.Enum):
    oferta = "oferta"
    negociacion = "negociacion"
    documentos = "documentos"
    firma = "firma"
    confirmado = "confirmado"


class SaleClosing(Base):
    __tablename__ = "sale_closings"
    __table_args__ = (
        UniqueConstraint("user_id", "property_id", name="uq_user_property_closing"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    property_id = Column(UUID(as_uuid=True), ForeignKey("properties.id"), nullable=False)
    post_visit_report_id = Column(UUID(as_uuid=True), ForeignKey("post_visit_reports.id"), nullable=True)
    step = Column(Enum(ClosingStep), default=ClosingStep.oferta, nullable=False)
    offer_price = Column(Float, nullable=False)
    counter_offer_price = Column(Float, nullable=True)
    agreed_price = Column(Float, nullable=True)
    negotiation_message = Column(String, nullable=True)
    documents = Column(JSON, nullable=False, default=dict)
    legal_summary = Column(String, nullable=True)
    signed = Column(Boolean, default=False, nullable=False)
    signed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    message = Column(String, nullable=False)
    type = Column(String, default="info")   # info | success | warning
    read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
