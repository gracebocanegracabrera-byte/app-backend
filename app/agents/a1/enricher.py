from app.core.ai_gateway import ai_json
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

ENRICH_PROMPT = """Eres un experto en el mercado inmobiliario de Trujillo, Peru.
Dado este listing de propiedad, extrae y normaliza los campos faltantes.
Retorna SOLO JSON válido (sin markdown):
{{
  "district": "nombre del distrito de Trujillo o null",
  "property_type": "departamento|casa|terreno|oficina o null",
  "bedrooms": número entero o null,
  "area_m2": número decimal o null,
  "zone": "Trujillo Centro|Zona Residencial|Zona Golf|Zona Playera|Zona Sur|Zona Este|Zona Norte o null"
}}

Título del listing: {title}
Precio: {price}
Datos actuales: distrito={district}, tipo={property_type}, área={area_m2}m², dorms={bedrooms}

Si no puedes inferir un campo con alta confianza, usa null."""


async def enrich_property(prop: dict) -> dict:
    missing = prop.get("district") is None or prop.get("bedrooms") is None or prop.get("area_m2") is None
    if not missing:
        return prop

    try:
        enriched = await ai_json(
            model=settings.MODEL_A1,
            messages=[
                {
                    "role": "user",
                    "content": ENRICH_PROMPT.format(
                        title=prop.get("title", ""),
                        price=f"S/ {prop.get('price', 'no especificado')}",
                        district=prop.get("district"),
                        property_type=prop.get("property_type"),
                        area_m2=prop.get("area_m2"),
                        bedrooms=prop.get("bedrooms"),
                    ),
                }
            ],
            max_tokens=150,
        )
        for key, value in enriched.items():
            if value is not None and prop.get(key) is None:
                prop[key] = value
    except Exception as e:
        logger.warning(f"Enriquecimiento IA falló para {prop.get('title', '')}: {e}")

    return prop
