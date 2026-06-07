"""
A5 CommunicatorService — redacta y registra mensajes personalizados con Mistral 7B.
Los mensajes se guardan en tabla `notifications` y se loguean como email simulado.
No envía email real (MVP académico).
"""
import logging
from uuid import UUID
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.ai_gateway import ai_complete
from app.core.config import settings
from app.models.crm import Notification

logger = logging.getLogger("communications")

MESSAGE_PROMPT = """Eres un asesor inmobiliario profesional de André Bringas Corporation.
Redacta un mensaje corto (máximo 3 oraciones) en español para {lead_name} sobre:
Situación: {situation}
Propiedad de interés: {property_info}
Tono: profesional pero amigable, personalizado, en primera persona del asesor.
Solo el mensaje, sin asunto ni firma."""

# Statuses que generan notificación al usuario
NOTIFY_STATUSES = {"contacted", "interested", "scheduled", "closed_won", "closed_lost"}

SITUATION_MAP = {
    "contacted": "el asesor acaba de intentar contactarte para coordinar una visita",
    "interested": "se registró tu interés formal en la propiedad",
    "scheduled": "tu visita ha sido confirmada en el calendario",
    "closed_won": "la operación fue cerrada exitosamente, felicitaciones",
    "closed_lost": "la operación fue cerrada, pero podemos explorar otras opciones",
}


class CommunicatorService:

    async def draft_message(
        self, lead_name: str, situation: str, property_info: str
    ) -> str:
        """Llama a Mistral 7B para redactar mensaje personalizado. Fallback a texto fijo."""
        try:
            prompt = MESSAGE_PROMPT.format(
                lead_name=lead_name,
                situation=situation,
                property_info=property_info,
            )
            return await ai_complete(
                model=settings.MODEL_A5,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
            )
        except Exception as e:
            logger.warning(f"[A5 draft_message] IA falló, usando fallback: {e}")
            return (
                f"Estimado/a {lead_name}, le informamos sobre la actualización "
                f"de su solicitud respecto a {property_info}. "
                f"Cualquier consulta, estamos a su disposición."
            )

    def _log_simulated_email(
        self, user_id: UUID, title: str, message: str
    ) -> None:
        logger.info(
            f"[EMAIL SIMULADO] user_id={user_id} | título='{title}' | "
            f"mensaje='{message[:100]}{'...' if len(message) > 100 else ''}'"
        )

    def save_notification(
        self,
        user_id: UUID,
        title: str,
        message: str,
        notif_type: str,
        db: AsyncSession,
    ) -> Notification:
        """Inserta Notification en sesión (sin commit — el caller hace commit)."""
        notif = Notification(
            user_id=user_id,
            title=title,
            message=message,
            type=notif_type,
        )
        db.add(notif)
        self._log_simulated_email(user_id, title, message)
        return notif

    async def notify_appointment_created(
        self,
        user_id: UUID,
        lead_name: str,
        scheduled_at: datetime,
        property_info: str,
        db: AsyncSession,
    ) -> None:
        """Notifica al usuario que su visita fue agendada."""
        situation = (
            f"se agendó una visita para el "
            f"{scheduled_at.strftime('%d/%m/%Y a las %H:%M')}"
        )
        message = await self.draft_message(lead_name, situation, property_info)
        self.save_notification(user_id, "Cita agendada ✅", message, "success", db)

    async def notify_lead_status_change(
        self,
        user_id: UUID,
        lead_name: str,
        new_status: str,
        property_info: str,
        db: AsyncSession,
    ) -> None:
        """Notifica cambio de estado del lead (solo statuses relevantes)."""
        if new_status not in NOTIFY_STATUSES:
            return
        situation = SITUATION_MAP.get(
            new_status, f"el estado de tu solicitud cambió a {new_status}"
        )
        message = await self.draft_message(lead_name, situation, property_info)
        notif_type = "success" if new_status == "closed_won" else "info"
        self.save_notification(
            user_id, "Actualización de tu solicitud", message, notif_type, db
        )


communicator = CommunicatorService()
