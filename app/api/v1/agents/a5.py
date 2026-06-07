import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from uuid import UUID
from typing import Literal, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.core.database import get_db
from app.core.auth import get_current_user
from app.agents.a5.service import agent_a5
from app.agents.a5.scheduler import scheduling_agent
from app.agents.a5.communicator import communicator
from app.models.crm import Lead, Appointment, LeadStatus, AppointmentStatus, Notification
from app.models.property import Property
from app.models.user import User
from app.schemas.a5 import (
    LeadCreate, LeadUpdate, LeadResponse,
    AppointmentCreate, AppointmentResponse, SlotConflictResponse,
    NotificationResponse, NotifyManualRequest,
    PostVisitReportUpsert, PostVisitReportResponse,
    ClosingActionRequest, SaleClosingResponse,
)
from datetime import datetime

router = APIRouter(prefix="/agents/a5", tags=["agent-a5"])
logger = logging.getLogger("communications")


class SchedulingChatRequest(BaseModel):
    property_id: UUID
    messages: list[dict]


# ── CHAT AGENDAMIENTO ──────────────────────────────────────


@router.post("/chat")
async def scheduling_chat(
    data: SchedulingChatRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Chat de agendamiento para cliente. Devuelve respuesta A5 + appointment si se confirmó."""
    prop_result = await db.execute(
        select(Property).where(Property.id == data.property_id)
    )
    prop = prop_result.scalar_one_or_none()
    if not prop:
        raise HTTPException(404, "Propiedad no encontrada")

    address_parts = [prop.zone, prop.district]
    address = ", ".join(p for p in address_parts if p) or "Trujillo"

    property_context = {
        "name": prop.title,
        "address": address,
        "price": f"S/ {prop.price:,.0f}" if prop.price else "N/A",
        "score": "N/A",
    }

    result = await scheduling_agent.chat(
        data.messages, data.property_id, property_context, current_user.id, db
    )
    return result


# ── LEADS ──────────────────────────────────────────────────


@router.post("/leads", response_model=LeadResponse, status_code=201)
async def create_lead(
    data: LeadCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await agent_a5.create_lead(current_user.id, data.property_id, db)


@router.get("/leads", response_model=list[LeadResponse])
async def list_leads(
    property_id: Optional[UUID] = Query(None),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await agent_a5.get_leads(
        current_user.id, current_user.role, property_id, db
    )


@router.patch("/leads/{lead_id}", response_model=LeadResponse)
async def update_lead(
    lead_id: UUID,
    data: LeadUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        lead = await agent_a5.update_lead(
            lead_id, current_user.id, current_user.role,
            data.status, data.notes, db,
        )
    except ValueError:
        raise HTTPException(404, "Lead no encontrado")

    # Notificación de cambio de estado (no bloquea la respuesta)
    if data.status:
        try:
            user_result = await db.execute(select(User).where(User.id == lead.user_id))
            lead_user = user_result.scalar_one_or_none()
            lead_name = lead_user.name if lead_user else "Cliente"

            prop_result = await db.execute(
                select(Property).where(Property.id == lead.property_id)
            )
            prop = prop_result.scalar_one_or_none()
            property_info = (
                f"{prop.property_type} en {prop.district}" if prop else "la propiedad"
            )

            await communicator.notify_lead_status_change(
                lead.user_id, lead_name, data.status, property_info, db
            )
            await db.commit()
        except Exception as e:
            logger.warning(f"Notificación update_lead falló (no crítico): {e}")

    return lead


# ── APPOINTMENTS ───────────────────────────────────────────


@router.post(
    "/appointments",
    response_model=AppointmentResponse,
    status_code=201,
    responses={409: {"model": SlotConflictResponse}},
)
async def create_appointment(
    data: AppointmentCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Crea appointment con manejo de conflictos de slot.
    Auto-crea o recupera el lead del usuario para esa propiedad.
    Si slot ya está tomado → HTTP 409 con alternativas.
    Al crear appointment → lead.status pasa a "scheduled".
    """
    lead = await agent_a5.create_lead(current_user.id, data.property_id, db)

    appointment = Appointment(
        lead_id=lead.id,
        property_id=data.property_id,
        scheduled_at=data.scheduled_at,
        status=AppointmentStatus.pending,
    )
    db.add(appointment)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        available = await agent_a5.get_available_slots(
            data.property_id, data.scheduled_at, db
        )
        raise HTTPException(
            409,
            detail={
                "detail": "slot_taken",
                "available_slots": [s.isoformat() for s in available],
            },
        )

    lead.status = LeadStatus.scheduled
    await db.commit()
    await db.refresh(appointment)

    # Notificación de cita creada (no bloquea la respuesta)
    try:
        prop_result = await db.execute(
            select(Property).where(Property.id == data.property_id)
        )
        prop = prop_result.scalar_one_or_none()
        property_info = (
            f"{prop.property_type} en {prop.district}" if prop else "la propiedad"
        )
        await communicator.notify_appointment_created(
            current_user.id, current_user.name,
            appointment.scheduled_at, property_info, db,
        )
        await db.commit()
    except Exception as e:
        logger.warning(f"Notificación appointment falló (no crítico): {e}")

    return appointment


@router.get("/appointments", response_model=list[AppointmentResponse])
async def list_appointments(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await agent_a5.get_appointments(current_user.id, current_user.role, db)


@router.patch("/appointments/{appt_id}", response_model=AppointmentResponse)
async def update_appointment(
    appt_id: UUID,
    status: Literal["pending", "confirmed", "cancelled", "completed"] = Query(...),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Appointment).where(Appointment.id == appt_id)
    )
    appt = result.scalar_one_or_none()
    if not appt:
        raise HTTPException(404, "Cita no encontrada")

    # C2: Buyer solo puede modificar sus propias citas; admin/advisor pueden cualquiera
    if current_user.role not in ("admin", "advisor"):
        lead_result = await db.execute(
            select(Lead).where(
                Lead.id == appt.lead_id,
                Lead.user_id == current_user.id,
            )
        )
        if not lead_result.scalar_one_or_none():
            raise HTTPException(403, "No tienes permiso para modificar esta cita")

    appt.status = AppointmentStatus(status)

    if status == "completed":
        lead_result = await db.execute(
            select(Lead).where(Lead.id == appt.lead_id)
        )
        lead = lead_result.scalar_one_or_none()
        if lead:
            lead.status = LeadStatus.visited

    await db.commit()
    await db.refresh(appt)
    return appt


# ── POST-VISITA ────────────────────────────────────────────


@router.post("/post-visit-reports", response_model=PostVisitReportResponse, status_code=201)
async def upsert_post_visit_report(
    data: PostVisitReportUpsert,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Crea o actualiza el reporte post-visita del usuario para una propiedad."""
    report = await agent_a5.upsert_post_visit_report(current_user.id, data, db)
    return await agent_a5.get_post_visit_report(current_user.id, report.property_id, db)


@router.get("/post-visit-reports", response_model=list[PostVisitReportResponse])
async def list_post_visit_reports(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lista reportes post-visita (propios para buyer/advisor; todos para admin/advisor)."""
    return await agent_a5.get_post_visit_reports(current_user.id, current_user.role, db)


@router.get("/post-visit-reports/{property_id}", response_model=Optional[PostVisitReportResponse])
async def get_post_visit_report(
    property_id: UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Obtiene el reporte post-visita del usuario autenticado para una propiedad (o null)."""
    return await agent_a5.get_post_visit_report(current_user.id, property_id, db)


# ── CIERRE DE VENTA ────────────────────────────────────────


@router.get("/closings", response_model=list[SaleClosingResponse])
async def list_closings(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lista todos los procesos de cierre de venta del usuario autenticado (puede tener varios)."""
    return await agent_a5.get_closings(current_user.id, db)


@router.get("/closings/{property_id}", response_model=SaleClosingResponse)
async def get_closing(
    property_id: UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Obtiene (o inicia) el proceso de cierre de venta para una propiedad.

    Solo puede iniciarse si el usuario registró una post-visita con decisión "oferta".
    """
    try:
        await agent_a5.get_or_create_closing(current_user.id, property_id, db)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return await agent_a5.get_closing(current_user.id, property_id, db)


@router.post("/closings/{property_id}/actions", response_model=SaleClosingResponse)
async def perform_closing_action(
    property_id: UUID,
    data: ClosingActionRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Avanza el flujo de cierre: enviar oferta, negociar, revisar documentos, firmar."""
    try:
        await agent_a5.perform_closing_action(
            current_user.id, property_id, data.action, data.counter_price, data.document_key, db
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await agent_a5.get_closing(current_user.id, property_id, db)


# ── NOTIFICATIONS ──────────────────────────────────────────


@router.get("/notifications", response_model=list[NotificationResponse])
async def list_notifications(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lista las últimas 20 notificaciones del usuario autenticado."""
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(20)
    )
    return result.scalars().all()


@router.patch("/notifications/{notif_id}/read", status_code=204)
async def mark_notification_read(
    notif_id: UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Marca notificación como leída (solo si pertenece al usuario)."""
    result = await db.execute(
        select(Notification).where(Notification.id == notif_id)
    )
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(404, "Notificación no encontrada")
    if notif.user_id != current_user.id:
        raise HTTPException(403, "No tienes permiso para acceder a esta notificación")
    notif.read = True
    await db.commit()


@router.post("/notify", response_model=NotificationResponse, status_code=201)
async def notify_manual(
    data: NotifyManualRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Envío manual de notificación para el usuario autenticado (demo)."""
    notif = communicator.save_notification(
        current_user.id, data.title, data.message, data.type, db
    )
    await db.commit()
    await db.refresh(notif)
    return notif
