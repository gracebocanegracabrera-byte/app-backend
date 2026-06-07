from uuid import UUID
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.ai_gateway import ai_complete, ai_json
from app.core.config import settings
from app.models.profile import ChatMessage, UserProfile
from app.agents.a3.prompts import SYSTEM_PROMPT_A3, EXTRACTION_PROMPT_A3, FIRST_MESSAGE_A3


# Umbrales de validación rent vs sale (Perú, S/.)
# Alquiler típico: S/500 a S/20,000/mes → > 30k probablemente es venta
# Venta típico: S/50,000 a S/3,000,000 → < 5k probablemente es alquiler mensual
_RENT_MAX_THRESHOLD = 30_000
_SALE_MIN_THRESHOLD = 5_000


def _detect_purpose_inconsistency(prefs: dict) -> Optional[tuple[str, str]]:
    """
    Detecta inconsistencias entre purpose y rango de precios.
    Retorna (purpose_corregido, mensaje) si hay inconsistencia, None si OK.
    """
    purpose = prefs.get("purpose")
    price_min = prefs.get("price_min")
    price_max = prefs.get("price_max")

    if purpose == "alquiler":
        # Alquiler con precio > 30k → probablemente es venta
        big_price = None
        if isinstance(price_max, (int, float)) and price_max > _RENT_MAX_THRESHOLD:
            big_price = price_max
        elif isinstance(price_min, (int, float)) and price_min > _RENT_MAX_THRESHOLD:
            big_price = price_min
        if big_price:
            return (
                "compra",
                f"Detecté que tu presupuesto (S/{big_price:,.0f}) parece de compra, "
                f"no de alquiler. Ajusté automáticamente tu perfil a búsqueda de compra.",
            )

    elif purpose == "compra":
        # Compra con precio < 5k → probablemente es alquiler mensual
        small_price = None
        if isinstance(price_max, (int, float)) and 0 < price_max < _SALE_MIN_THRESHOLD:
            small_price = price_max
        elif isinstance(price_min, (int, float)) and 0 < price_min < _SALE_MIN_THRESHOLD:
            small_price = price_min
        if small_price:
            return (
                "alquiler",
                f"Detecté que tu presupuesto (S/{small_price:,.0f}) parece de alquiler "
                f"mensual, no de compra. Ajusté automáticamente tu perfil a búsqueda de alquiler.",
            )

    return None


class AgentA3Service:

    async def get_history(self, user_id: UUID, db: AsyncSession) -> list[dict]:
        # Traer los 20 más recientes y revertir para orden cronológico (más antiguo primero)
        result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.user_id == user_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(20)
        )
        msgs = list(reversed(result.scalars().all()))
        return [{"role": m.role, "content": m.content} for m in msgs]

    async def chat(
        self, user_id: UUID, message: str, db: AsyncSession, user_name: str = ""
    ) -> dict:
        """Returns {"response": str, "auto_correction": str | None}."""
        name = user_name or "amigo"

        # Detectar primer mensaje antes de guardar — si no hay historial, retornar saludo hardcoded
        existing_history = await self.get_history(user_id, db)
        is_first = len(existing_history) == 0

        db.add(ChatMessage(user_id=user_id, role="user", content=message))
        await db.flush()

        if is_first:
            greeting = FIRST_MESSAGE_A3.format(user_name=name)
            db.add(ChatMessage(user_id=user_id, role="assistant", content=greeting))
            await db.commit()
            return {"response": greeting, "auto_correction": None}

        history = await self.get_history(user_id, db)
        system_prompt = SYSTEM_PROMPT_A3.format(user_name=name)

        response_text = await ai_complete(
            model=settings.MODEL_A3,
            messages=history,
            system=system_prompt,
            max_tokens=80,
        )

        # Garantía dura: truncar al primer signo de interrogación para evitar múltiples preguntas
        if "?" in response_text:
            response_text = response_text[: response_text.index("?") + 1].strip()

        db.add(ChatMessage(user_id=user_id, role="assistant", content=response_text))
        await db.commit()

        auto_correction: Optional[str] = None
        if len(history) >= 4:
            auto_correction = await self.update_profile(
                user_id,
                history + [{"role": "assistant", "content": response_text}],
                db,
            )

        return {"response": response_text, "auto_correction": auto_correction}

    async def update_profile(
        self, user_id: UUID, history: list[dict], db: AsyncSession
    ) -> Optional[str]:
        """
        Extrae perfil del chat y lo guarda.
        Aplica auto-corrección de purpose si los precios son inconsistentes.
        Retorna mensaje de auto-corrección o None.
        """
        conversation_text = "\n".join(
            [f"{m['role']}: {m['content']}" for m in history]
        )

        profile_data = await ai_json(
            model=settings.MODEL_A3,
            messages=[
                {
                    "role": "user",
                    "content": EXTRACTION_PROMPT_A3.format(
                        conversation=conversation_text
                    ),
                }
            ],
            max_tokens=300,
        )
        if not profile_data:
            return None

        fields = [
            "zone", "price_min", "price_max", "property_type",
            "bedrooms", "area_m2_min", "purpose",
        ]
        filled = sum(1 for f in fields if profile_data.get(f) is not None)
        completeness = round((filled / len(fields)) * 100, 1)

        # ── Auto-corrección purpose vs precios (safety net del prompt) ──
        inconsistency = _detect_purpose_inconsistency(profile_data)
        auto_correction_msg: Optional[str] = None
        if inconsistency:
            corrected_purpose, msg = inconsistency
            if profile_data.get("purpose") != corrected_purpose:
                profile_data["purpose"] = corrected_purpose
                auto_correction_msg = msg

        result = await db.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()

        if profile:
            # Merge: solo sobreescribe campos con valor real (no borra con null
            # datos que ya se tenían de turnos anteriores)
            merged = dict(profile.preferences or {})
            for k, v in profile_data.items():
                if v is not None:
                    merged[k] = v
            # Auto-corrección siempre sobrescribe purpose si fue detectado
            if inconsistency:
                merged["purpose"] = profile_data["purpose"]
            profile.preferences = merged
            profile.completeness_pct = completeness
        else:
            db.add(
                UserProfile(
                    user_id=user_id,
                    preferences=profile_data,
                    completeness_pct=completeness,
                )
            )

        await db.commit()
        return auto_correction_msg

    async def get_profile(
        self, user_id: UUID, db: AsyncSession
    ) -> Optional[UserProfile]:
        result = await db.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        return result.scalar_one_or_none()


agent_a3 = AgentA3Service()
