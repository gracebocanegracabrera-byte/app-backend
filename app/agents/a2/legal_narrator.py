from app.core.ai_gateway import ai_complete
from app.core.config import settings

LEGAL_NARRATIVE_PROMPT = """Eres un asesor inmobiliario peruano.
Explica en 2-3 oraciones, en español simple, el resultado de esta evaluación legal de una propiedad.
Sé claro, honesto y útil. No uses jerga legal compleja.

Semáforo: {status_label}
Puntaje legal: {score}/100
Riesgos identificados: {risks}
Notas positivas: {positive_notes}

Escribe directamente al comprador. Si hay riesgos, menciona qué verificar.
Si está bien, tranquiliza al comprador. Termina con el disclaimer académico en una línea separada."""

STATUS_LABELS = {"green": "APROBADO", "yellow": "CON OBSERVACIONES", "red": "REQUIERE REVISIÓN"}


async def generate_legal_narrative(status: str, score: int, risks: list, positive_notes: list) -> str:
    prompt = LEGAL_NARRATIVE_PROMPT.format(
        status_label=STATUS_LABELS.get(status, status),
        score=score,
        risks="; ".join(risks) if risks else "Ninguno identificado",
        positive_notes="; ".join(positive_notes) if positive_notes else "No aplica",
    )
    return await ai_complete(
        model=settings.MODEL_A2,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=250,
    )
