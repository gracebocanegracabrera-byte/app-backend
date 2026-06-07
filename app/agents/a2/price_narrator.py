from app.core.ai_gateway import ai_complete
from app.core.config import settings

PRICE_NARRATIVE_PROMPT = """Eres un analista inmobiliario peruano experto en el mercado de Trujillo.
Dado el siguiente análisis de precio de una propiedad, redacta una explicación
clara y concisa (máximo 3 oraciones) en español para el comprador:

Propiedad: {district}, {area_m2} m², precio pedido S/ {asking_price:,}
Precio referencial zona: S/ {ref_price:,} (S/ {ref_m2}/m²)
Diferencia: {diff_pct:+.1f}% vs mercado
Veredicto: {verdict}

Redacta la explicación como si hablaras directamente al comprador.
Sé honesto pero constructivo. No repitas los números tal cual, interprétalos."""

async def generate_price_narrative(
    district: str,
    area_m2: float,
    asking_price: float,
    ref_price: float,
    ref_m2: float,
    diff_pct: float,
    verdict: str,
) -> str:
    prompt = PRICE_NARRATIVE_PROMPT.format(
        district=district or "Trujillo",
        area_m2=area_m2,
        asking_price=asking_price,
        ref_price=ref_price,
        ref_m2=ref_m2,
        diff_pct=diff_pct,
        verdict=verdict,
    )
    return await ai_complete(
        model=settings.MODEL_A2,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
    )
