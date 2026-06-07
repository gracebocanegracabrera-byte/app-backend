"""
SchedulingAgent — A5 chat de agendamiento con Mistral 7B.

_get_slot_context: mapea keywords de fecha a la fecha correcta (no siempre "mañana").
_try_create_appointment: usa ai_json para extraer fecha+hora y crea appointment en BD.
"""
import re
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.core.ai_gateway import ai_complete, ai_json
from app.core.config import settings
from app.agents.a5.service import agent_a5
from app.agents.a5.communicator import communicator
from app.agents.a5.prompts import SCHEDULING_SYSTEM_PROMPT
from app.models.user import User
from app.models.property import Property
from app.models.crm import LeadStatus
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID


# Trujillo/Perú no usa horario de verano — UTC-5 todo el año.
# Las horas de visita (9-18) que el usuario y A5 negocian son hora LOCAL de Trujillo,
# no UTC — hay que anclarlas a este huso para que scheduled_at represente el instante
# correcto y el frontend (que formatea en hora local del navegador) muestre la MISMA
# hora que A5 confirmó. Usar timezone.utc aquí desplazaba la hora mostrada -5h.
PERU_TZ = timezone(timedelta(hours=-5))


# Mapeo día de semana (es) → índice Python (0=lunes)
_DAY_MAP = {
    "lunes": 0,
    "martes": 1,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sábado": 5,
    "domingo": 6,
}


def _next_occurrence(weekday: int) -> datetime:
    """Retorna el próximo día de la semana (hora local Trujillo). Si hoy es ese día, retorna la siguiente semana."""
    today = datetime.now(tz=PERU_TZ)
    days_ahead = weekday - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return today + timedelta(days=days_ahead)


_HOUR_PATTERNS = [
    re.compile(r'a las\s+(\d{1,2})\s*(am|pm|de la mañana|de la tarde)?', re.IGNORECASE),
    re.compile(r'(\d{1,2})\s*(am|pm)', re.IGNORECASE),
    re.compile(r'(\d{1,2}):00\s*(am|pm|hrs?|horas)?', re.IGNORECASE),
]
_DDMM_RE = re.compile(r'\b(\d{1,2})/(\d{1,2})\b')


def _extract_hour_fallback(text: str) -> int | None:
    """Regex de respaldo — modelos :free fallan ai_json a menudo, pero el texto
    de confirmación casi siempre trae 'a las HH' o 'HHam/pm'."""
    for pattern in _HOUR_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        hour = int(m.group(1))
        suffix = (m.group(2) or "").lower()
        if ("pm" in suffix or "tarde" in suffix) and hour < 12:
            hour += 12
        if "am" in suffix and hour == 12:
            hour = 0
        if 9 <= hour <= 18:
            return hour
    return None


def _extract_date_fallback(text: str) -> datetime | None:
    """Regex/keyword de respaldo para fecha — espejo de _get_slot_context."""
    lower = text.lower()
    if "mañana" in lower:
        return datetime.now(tz=PERU_TZ) + timedelta(days=1)
    for name, weekday in _DAY_MAP.items():
        if name in lower:
            return _next_occurrence(weekday)
    m = _DDMM_RE.search(text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        today = datetime.now(tz=PERU_TZ)
        try:
            candidate = datetime(today.year, month, day, tzinfo=PERU_TZ)
        except ValueError:
            return None
        if candidate.date() < today.date():
            candidate = candidate.replace(year=today.year + 1)
        return candidate
    return None


class SchedulingAgent:

    async def chat(
        self,
        messages: list[dict],
        property_id: UUID,
        property_context: dict,
        user_id: UUID,
        db: AsyncSession,
    ) -> dict:
        """
        Procesa un turno de chat de agendamiento.
        Retorna: { "message": str, "appointment_created": bool, "appointment": dict|None }
        """
        system = SCHEDULING_SYSTEM_PROMPT.format(
            property_name=property_context.get("name", ""),
            property_address=property_context.get("address", ""),
            price=property_context.get("price", ""),
            score=property_context.get("score", ""),
        )

        slot_context = await self._get_slot_context(messages, property_id, db)
        if slot_context:
            system += f"\n\nSLOTS DISPONIBLES: {slot_context}"

        response_text = await ai_complete(
            model=settings.MODEL_A5,
            messages=messages,
            system=system,
            max_tokens=300,
        )

        appointment_data = await self._try_create_appointment(
            response_text, messages, property_id, user_id, db
        )

        return {
            "message": response_text,
            "appointment_created": appointment_data is not None,
            "appointment": appointment_data,
        }

    async def _get_slot_context(
        self, messages: list, property_id: UUID, db: AsyncSession
    ) -> str | None:
        """
        Extrae fecha del último mensaje y retorna slots disponibles para ESA fecha.
        Corrige H2 (tz naive) y M1 (siempre mostraba mañana ignorando el día pedido).
        """
        last_msg = messages[-1].get("content", "").lower() if messages else ""

        target_date: datetime | None = None

        if "mañana" in last_msg:
            target_date = datetime.now(tz=PERU_TZ) + timedelta(days=1)
        else:
            for name, weekday in _DAY_MAP.items():
                if name in last_msg:
                    target_date = _next_occurrence(weekday)
                    break

        if target_date is None and any(k in last_msg for k in ("próxima", "esta semana")):
            # Genérico: mostrar slots de mañana como referencia
            target_date = datetime.now(tz=PERU_TZ) + timedelta(days=1)

        if target_date is None:
            return None

        slots = await agent_a5.get_available_slots(property_id, target_date, db)
        if slots:
            date_str = target_date.strftime("%d/%m")
            return f"{date_str}: " + ", ".join(s.strftime("%H:%M") for s in slots[:5])
        return None

    async def _try_create_appointment(
        self,
        response: str,
        messages: list,
        property_id: UUID,
        user_id: UUID,
        db: AsyncSession,
    ) -> dict | None:
        """
        Si A5 confirmó una cita, extrae fecha+hora con ai_json y crea el appointment en BD.
        Retorna dict con datos del appointment, o None si no hay confirmación / no se puede extraer.
        """
        # Stems, no formas completas — el modelo a veces concuerda en género/número
        # ("queda agendada" vs "agendado", "confirmada" vs "confirmado") y la
        # comparación por substring fallaba, dejando la cita sin crear pese a que
        # A5 ya la había confirmado verbalmente al usuario.
        confirmation_keywords = ["confirmad", "agendad", "registrad", "quedamos", "queda agend"]
        if not any(k in response.lower() for k in confirmation_keywords):
            return None

        # Extraer fecha y hora de los últimos mensajes con ai_json.
        # IMPORTANTE: `response` (el mensaje de confirmación que A5 ACABA de generar)
        # todavía no está en `messages` — es el texto más confiable porque es la
        # última palabra de A5 sobre fecha/hora. Lo agregamos al final del historial
        # para que tanto el LLM como el regex de respaldo lo vean en último lugar
        # (y el regex de respaldo lo busque PRIMERO — ver más abajo) y no se
        # confundan con horas mencionadas anteriormente en la negociación
        # (p.ej. "3pm" propuesto al inicio vs "13:00" finalmente confirmado).
        conversation_text = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}"
            for m in messages[-6:]
        ) + f"\nassistant: {response}"
        extracted = await ai_json(
            model=settings.MODEL_A5,
            messages=[{
                "role": "user",
                "content": (
                    "Extrae SOLO la fecha y hora FINAL CONFIRMADA de esta conversación "
                    "(la última que acordaron, ignora propuestas anteriores que cambiaron). "
                    'Responde ÚNICAMENTE con JSON válido: {"date": "YYYY-MM-DD", "hour": <int 9-18>}\n\n'
                    + conversation_text
                ),
            }],
            max_tokens=50,
        )

        scheduled_at: datetime | None = None
        if extracted and "date" in extracted and "hour" in extracted:
            try:
                parts = [int(p) for p in str(extracted["date"]).split("-")]
                scheduled_at = datetime(
                    parts[0], parts[1], parts[2],
                    int(extracted["hour"]), 0, 0,
                    tzinfo=PERU_TZ,
                )
            except (ValueError, IndexError, TypeError):
                scheduled_at = None

        if scheduled_at is None:
            # ai_json falla seguido en modelos :free — respaldo con regex sobre
            # el texto crudo (fecha de "mañana"/día de semana + "a las HH").
            # `response` primero — es la confirmación final de A5, debe ganarle
            # a menciones anteriores tipo "3pm" que el usuario propuso y luego
            # cambió a "13:00". Los regex toman el PRIMER match.
            fallback_date = _extract_date_fallback(response + "\n" + conversation_text)
            fallback_hour = _extract_hour_fallback(response + "\n" + conversation_text)
            if fallback_date is not None and fallback_hour is not None:
                scheduled_at = fallback_date.replace(
                    hour=fallback_hour, minute=0, second=0, microsecond=0
                )

        if scheduled_at is None:
            return None  # Sin extracción → el frontend puede llamar POST /appointments directamente

        try:
            lead = await agent_a5.create_lead(user_id, property_id, db)
            appointment = await agent_a5.create_appointment(
                lead.id, property_id, scheduled_at, db
            )
            # Espejo del POST /appointments — sin esto el lead se queda en "new"
            # y el pipeline del CRM no refleja la cita recién creada por el chat.
            lead.status = LeadStatus.scheduled
            await db.commit()
            await db.refresh(appointment)
        except Exception:
            # Slot tomado u otro error — chat continúa, sin auto-booking
            return None

        # Notificación de cita agendada (no bloquea si falla)
        try:
            user_result = await db.execute(select(User).where(User.id == user_id))
            user = user_result.scalar_one_or_none()
            lead_name = user.name if user else "Cliente"

            prop_result = await db.execute(
                select(Property).where(Property.id == property_id)
            )
            prop = prop_result.scalar_one_or_none()
            property_info = (
                f"{prop.property_type} en {prop.district}" if prop else "la propiedad"
            )

            await communicator.notify_appointment_created(
                user_id, lead_name, appointment.scheduled_at, property_info, db
            )
            await db.commit()
        except Exception:
            pass  # Notificación no crítica

        return {
            "id": str(appointment.id),
            "lead_id": str(appointment.lead_id),
            "property_id": str(appointment.property_id),
            "scheduled_at": appointment.scheduled_at.isoformat(),
            "status": (
                appointment.status.value
                if hasattr(appointment.status, "value")
                else str(appointment.status)
            ),
        }


scheduling_agent = SchedulingAgent()
