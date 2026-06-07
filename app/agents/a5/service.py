from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.models.crm import (
    Lead, Appointment, LeadStatus, AppointmentStatus,
    PostVisitReport, VisitDecision, SaleClosing, ClosingStep,
)
from app.models.property import Property
from app.models.user import User
from app.core.ai_gateway import ai_complete
from app.core.config import settings
from datetime import datetime, timedelta
from typing import Optional


# Checklist fijo de documentos del proceso de cierre — (clave, etiqueta, estado inicial)
CLOSING_DOCUMENTS = [
    ("dni_comprador", "DNI comprador", "verified"),
    ("partida_sunarp", "Partida registral SUNARP", "verified"),
    ("carta_saneamiento", "Carta de saneamiento de deuda", "observed"),
    ("contrato_compraventa", "Contrato de compraventa", "pending"),
    ("escritura_notarial", "Escritura notarial", "pending"),
]


class AgentA5Service:

    async def create_lead(self, user_id: UUID, property_id: UUID, db: AsyncSession) -> Lead:
        existing = await db.execute(
            select(Lead).where(Lead.user_id == user_id, Lead.property_id == property_id)
        )
        lead = existing.scalar_one_or_none()
        if lead:
            return lead

        lead = Lead(user_id=user_id, property_id=property_id, status=LeadStatus.new)
        db.add(lead)
        await db.commit()
        await db.refresh(lead)
        return lead

    async def get_leads(self, user_id: UUID, role: str,
                        property_id: Optional[UUID], db: AsyncSession) -> list[dict]:
        query = (
            select(Lead, User.name, Property.title)
            .join(User, User.id == Lead.user_id)
            .join(Property, Property.id == Lead.property_id)
            .order_by(Lead.created_at.desc())
        )
        if role not in ("admin", "advisor"):
            query = query.where(Lead.user_id == user_id)
        if property_id:
            query = query.where(Lead.property_id == property_id)
        result = await db.execute(query)
        rows = result.all()
        leads = []
        for lead, user_name, property_title in rows:
            leads.append({
                "id": lead.id,
                "user_id": lead.user_id,
                "user_name": user_name,
                "property_id": lead.property_id,
                "property_title": property_title,
                "status": lead.status,
                "notes": lead.notes,
                "created_at": lead.created_at,
                "updated_at": lead.updated_at,
            })
        return leads

    async def update_lead(self, lead_id: UUID, user_id: UUID, role: str,
                          status: Optional[str], notes: Optional[str], db: AsyncSession) -> Lead:
        query = select(Lead).where(Lead.id == lead_id)
        if role not in ("admin", "advisor"):
            query = query.where(Lead.user_id == user_id)
        result = await db.execute(query)
        lead = result.scalar_one_or_none()
        if not lead:
            raise ValueError("Lead no encontrado")
        if status:
            lead.status = LeadStatus(status)
        if notes is not None:
            lead.notes = notes
        await db.commit()
        await db.refresh(lead)
        return lead

    async def create_appointment(self, lead_id: UUID, property_id: UUID,
                                 scheduled_at: datetime, db: AsyncSession) -> Appointment:
        appointment = Appointment(
            lead_id=lead_id,
            property_id=property_id,
            scheduled_at=scheduled_at,
            status=AppointmentStatus.pending,
        )
        db.add(appointment)
        try:
            await db.commit()
            await db.refresh(appointment)
            return appointment
        except IntegrityError:
            await db.rollback()
            raise

    async def get_appointments(self, user_id: UUID, role: str, db: AsyncSession) -> list[dict]:
        query = (
            select(Appointment, Property.title, User.name)
            .join(Lead, Lead.id == Appointment.lead_id)
            .join(Property, Property.id == Appointment.property_id)
            .join(User, User.id == Lead.user_id)
            .order_by(Appointment.scheduled_at)
        )
        if role not in ("admin", "advisor"):
            query = query.where(Lead.user_id == user_id)
        result = await db.execute(query)
        rows = result.all()
        appointments = []
        for appt, property_title, user_name in rows:
            appointments.append({
                "id": appt.id,
                "lead_id": appt.lead_id,
                "property_id": appt.property_id,
                "property_title": property_title,
                "user_name": user_name,
                "scheduled_at": appt.scheduled_at,
                "status": appt.status,
                "created_at": appt.created_at,
            })
        return appointments

    async def upsert_post_visit_report(self, user_id: UUID, data, db: AsyncSession) -> PostVisitReport:
        existing = await db.execute(
            select(PostVisitReport).where(
                PostVisitReport.user_id == user_id,
                PostVisitReport.property_id == data.property_id,
            )
        )
        report = existing.scalar_one_or_none()
        if report is None:
            report = PostVisitReport(user_id=user_id, property_id=data.property_id)
            db.add(report)

        report.appointment_id = data.appointment_id
        report.ratings = data.ratings
        report.notes = data.notes
        report.decision = VisitDecision(data.decision) if data.decision else None
        report.offer_price = data.offer_price
        report.offer_condition = data.offer_condition
        report.offer_deadline = data.offer_deadline
        report.offer_notes = data.offer_notes

        await db.commit()
        await db.refresh(report)
        return report

    async def get_post_visit_report(self, user_id: UUID, property_id: UUID, db: AsyncSession) -> Optional[dict]:
        result = await db.execute(
            select(PostVisitReport, Property.title)
            .join(Property, Property.id == PostVisitReport.property_id)
            .where(PostVisitReport.user_id == user_id, PostVisitReport.property_id == property_id)
        )
        row = result.first()
        if not row:
            return None
        return self._serialize_report(*row)

    async def get_post_visit_reports(self, user_id: UUID, role: str, db: AsyncSession) -> list[dict]:
        query = (
            select(PostVisitReport, Property.title)
            .join(Property, Property.id == PostVisitReport.property_id)
            .order_by(PostVisitReport.updated_at.desc().nullslast(), PostVisitReport.created_at.desc())
        )
        if role not in ("admin", "advisor"):
            query = query.where(PostVisitReport.user_id == user_id)
        result = await db.execute(query)
        return [self._serialize_report(report, title) for report, title in result.all()]

    @staticmethod
    def _serialize_report(report: PostVisitReport, property_title: Optional[str]) -> dict:
        return {
            "id": report.id,
            "user_id": report.user_id,
            "property_id": report.property_id,
            "property_title": property_title,
            "appointment_id": report.appointment_id,
            "ratings": report.ratings,
            "notes": report.notes,
            "decision": report.decision.value if report.decision else None,
            "offer_price": report.offer_price,
            "offer_condition": report.offer_condition,
            "offer_deadline": report.offer_deadline,
            "offer_notes": report.offer_notes,
            "created_at": report.created_at,
            "updated_at": report.updated_at,
        }

    async def get_available_slots(self, property_id: UUID, date: datetime,
                                  db: AsyncSession) -> list[datetime]:
        all_slots = [date.replace(hour=h, minute=0, second=0, microsecond=0)
                     for h in range(9, 19)]
        taken = await db.execute(
            select(Appointment.scheduled_at).where(
                Appointment.property_id == property_id,
                Appointment.scheduled_at.between(
                    date.replace(hour=0, minute=0, second=0, microsecond=0),
                    date.replace(hour=23, minute=59, second=59, microsecond=999999)
                )
            )
        )
        taken_set = {r[0] for r in taken.fetchall()}
        return [s for s in all_slots if s not in taken_set]

    # ------------------------------------------------------------------
    # Cierre de venta — A5 (negociación) + A2 (revisión documental/legal)
    # ------------------------------------------------------------------

    async def get_or_create_closing(self, user_id: UUID, property_id: UUID,
                                     db: AsyncSession) -> SaleClosing:
        existing = await db.execute(
            select(SaleClosing).where(
                SaleClosing.user_id == user_id,
                SaleClosing.property_id == property_id,
            )
        )
        closing = existing.scalar_one_or_none()
        if closing:
            return closing

        # Solo se puede iniciar el cierre desde una post-visita con decisión "oferta"
        report_row = await db.execute(
            select(PostVisitReport).where(
                PostVisitReport.user_id == user_id,
                PostVisitReport.property_id == property_id,
            )
        )
        report = report_row.scalar_one_or_none()
        if not report or report.decision != VisitDecision.oferta:
            raise ValueError("No existe una oferta registrada para esta propiedad")

        property_row = await db.execute(select(Property).where(Property.id == property_id))
        prop = property_row.scalar_one_or_none()
        if not prop:
            raise ValueError("Propiedad no encontrada")

        offer_price = report.offer_price or prop.price or 0

        closing = SaleClosing(
            user_id=user_id,
            property_id=property_id,
            post_visit_report_id=report.id,
            step=ClosingStep.oferta,
            offer_price=offer_price,
            documents={},
        )
        db.add(closing)
        await db.commit()
        await db.refresh(closing)
        return closing

    async def perform_closing_action(self, user_id: UUID, property_id: UUID,
                                      action: str, counter_price: Optional[float],
                                      document_key: Optional[str],
                                      db: AsyncSession) -> SaleClosing:
        result = await db.execute(
            select(SaleClosing).where(
                SaleClosing.user_id == user_id,
                SaleClosing.property_id == property_id,
            )
        )
        closing = result.scalar_one_or_none()
        if not closing:
            raise ValueError("No se encontró un proceso de cierre para esta propiedad")

        property_row = await db.execute(select(Property).where(Property.id == property_id))
        prop = property_row.scalar_one_or_none()

        if action == "advance":
            if closing.step != ClosingStep.oferta:
                raise ValueError("La oferta ya fue enviada — esperando negociación")
            await self._start_negotiation(closing, prop, db)

        elif action in ("accept_counter", "counter_offer"):
            if closing.step != ClosingStep.negociacion:
                raise ValueError("No hay negociación activa")
            if action == "accept_counter":
                closing.agreed_price = closing.counter_offer_price
            else:
                if counter_price is None or counter_price <= 0:
                    raise ValueError("Debes indicar un monto de contraoferta válido")
                closing.agreed_price = counter_price
            await self._enter_documents(closing, db)

        elif action == "toggle_document":
            if closing.step != ClosingStep.documentos:
                raise ValueError("La revisión de documentos no está activa")
            if not document_key or document_key not in closing.documents:
                raise ValueError("Documento no reconocido")
            docs = dict(closing.documents)
            docs[document_key] = "verified"
            closing.documents = docs

        elif action == "proceed_to_signing":
            if closing.step != ClosingStep.documentos:
                raise ValueError("La revisión de documentos no está activa")
            if not all(status == "verified" for status in closing.documents.values()):
                raise ValueError("Aún hay documentos pendientes de verificación")
            closing.step = ClosingStep.firma

        elif action == "sign":
            if closing.step != ClosingStep.firma:
                raise ValueError("El contrato no está listo para firma")
            closing.signed = True
            closing.signed_at = datetime.utcnow()
            closing.step = ClosingStep.confirmado

        else:
            raise ValueError("Acción no soportada")

        await db.commit()
        await db.refresh(closing)
        return closing

    async def _start_negotiation(self, closing: SaleClosing, prop: Optional[Property],
                                  db: AsyncSession) -> None:
        listing_price = (prop.price if prop else None) or closing.offer_price
        counter = round(((closing.offer_price + listing_price) / 2) / 100) * 100
        closing.counter_offer_price = counter

        prompt = (
            f"Eres A5, asistente de negociación inmobiliaria en Trujillo, Perú. "
            f"El comprador ofreció S/ {closing.offer_price:,.0f} por una propiedad publicada en "
            f"S/ {listing_price:,.0f}. El vendedor respondió con una contraoferta de S/ {counter:,.0f}. "
            f"Redacta en máximo 3 frases una recomendación breve y concreta para el comprador "
            f"sobre si aceptar, contraofertar o declinar, en español, tono cercano y profesional."
        )
        try:
            message = await ai_complete(
                model=settings.MODEL_A5,
                messages=[{"role": "user", "content": prompt}],
                system="Eres un asistente de negociación inmobiliaria. Responde solo con la recomendación, sin saludos.",
                max_tokens=220,
            )
        except Exception:
            message = ""

        closing.negotiation_message = message.strip() or (
            f"La contraoferta de S/ {counter:,.0f} está dentro de un rango razonable frente a tu oferta "
            f"de S/ {closing.offer_price:,.0f}. Te recomiendo evaluar aceptarla o proponer un monto intermedio "
            f"antes de declinar."
        )
        closing.step = ClosingStep.negociacion

    async def _enter_documents(self, closing: SaleClosing, db: AsyncSession) -> None:
        closing.documents = {key: status for key, _, status in CLOSING_DOCUMENTS}

        prompt = (
            f"Eres A2, asistente legal inmobiliario en Trujillo, Perú (Ley 29733). "
            f"El comprador y vendedor acordaron un precio de S/ {closing.agreed_price:,.0f}. "
            f"Los documentos DNI y partida registral SUNARP ya fueron verificados sin observaciones. "
            f"La carta de saneamiento de deuda está en validación de firma notarial. "
            f"Faltan el contrato de compraventa y la escritura notarial. "
            f"Redacta en máximo 3 frases un resumen del estado legal del expediente y qué falta para "
            f"quedar listo para firma, en español, tono profesional. Aclara que es información referencial."
        )
        try:
            summary = await ai_complete(
                model=settings.MODEL_A2,
                messages=[{"role": "user", "content": prompt}],
                system="Eres un asistente legal inmobiliario. Responde solo con el resumen, sin saludos.",
                max_tokens=260,
            )
        except Exception:
            summary = ""

        closing.legal_summary = summary.strip() or (
            "Documentos principales verificados sin cargas ni hipotecas. La carta de saneamiento está en "
            "validación notarial; cuando se confirme, el expediente queda listo para firma. "
            "Información referencial — verifica el estado registral con SUNARP o un abogado habilitado."
        )
        closing.step = ClosingStep.documentos

    async def get_closings(self, user_id: UUID, db: AsyncSession) -> list[dict]:
        result = await db.execute(
            select(SaleClosing, Property.title)
            .join(Property, Property.id == SaleClosing.property_id)
            .where(SaleClosing.user_id == user_id)
            .order_by(SaleClosing.updated_at.desc().nullslast(), SaleClosing.created_at.desc())
        )
        return [self._serialize_closing(closing, title) for closing, title in result.all()]

    async def get_closing(self, user_id: UUID, property_id: UUID,
                          db: AsyncSession) -> Optional[dict]:
        result = await db.execute(
            select(SaleClosing, Property.title)
            .join(Property, Property.id == SaleClosing.property_id)
            .where(SaleClosing.user_id == user_id, SaleClosing.property_id == property_id)
        )
        row = result.first()
        if not row:
            return None
        return self._serialize_closing(*row)

    @staticmethod
    def _serialize_closing(closing: SaleClosing, property_title: Optional[str]) -> dict:
        return {
            "id": closing.id,
            "user_id": closing.user_id,
            "property_id": closing.property_id,
            "property_title": property_title,
            "step": closing.step.value,
            "offer_price": closing.offer_price,
            "counter_offer_price": closing.counter_offer_price,
            "agreed_price": closing.agreed_price,
            "negotiation_message": closing.negotiation_message,
            "documents": closing.documents,
            "legal_summary": closing.legal_summary,
            "signed": closing.signed,
            "signed_at": closing.signed_at,
            "created_at": closing.created_at,
            "updated_at": closing.updated_at,
        }


agent_a5 = AgentA5Service()
