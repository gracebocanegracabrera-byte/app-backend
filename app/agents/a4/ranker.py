from app.core.ai_gateway import ai_complete
from app.core.config import settings

RANKING_REASON_PROMPT = """Eres un analista de inversiones inmobiliarias peruano.
En máximo 2 oraciones, explica por qué esta propiedad {tag} para el perfil del comprador.
Sé específico con los factores más importantes. Habla directamente al comprador.

Propiedad: {property_type} en {district}
Precio: S/ {price:,} ({price_verdict})
Estado legal: {legal_status}
Score total: {score}/100
Factores: precio {s_price:.0f}pts | legal {s_legal:.0f}pts | perfil {s_profile:.0f}pts

Responde solo las 2 oraciones, sin introducción ni cierre."""


async def generate_ranking_rationale(
    property_type: str,
    district: str,
    price: float,
    price_verdict: str,
    legal_status: str,
    score: float,
    tag: str,
    breakdown: dict,
) -> str:
    prompt = RANKING_REASON_PROMPT.format(
        tag=tag.lower(),
        property_type=property_type or "Propiedad",
        district=district or "Trujillo",
        price=price or 0,
        price_verdict=price_verdict,
        legal_status=legal_status or "no evaluado",
        score=score,
        s_price=breakdown.get("precio", 0),
        s_legal=breakdown.get("legal", 0),
        s_profile=breakdown.get("match_perfil", 0),
    )
    return await ai_complete(
        model=settings.MODEL_A4,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=150,
    )
