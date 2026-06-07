"""Raspa propiedades REALES de InfoCasas.com.pe (Trujillo) y las carga en BD.

Ejecutar:
  cd app/backend
  python scripts/seed_properties.py

Fuente: infocasas.com.pe — propiedades reales de Trujillo, La Libertad.
No usa datos ficticios.
"""

import asyncio
import sys

sys.path.insert(0, ".")


async def main():
    from app.core.database import AsyncSessionLocal
    from app.agents.a1.service import agent_a1
    from app.agents.a1.scraper import scraper
    from app.models.property import Property
    from sqlalchemy import select, func

    print("Scrapeando propiedades reales de InfoCasas.com.pe...")
    print("(Trujillo, La Libertad — puede tomar 30-60 segundos)\n")

    # Muestra progreso durante el scraping
    raw = await scraper.scrape_all()

    if not raw:
        print("ERROR: No se obtuvieron propiedades del scraper.")
        print("Posibles causas: sin internet, sitio caido, o bloqueado.")
        return

    # Estadisticas de lo scrapeado
    types: dict = {}
    lt_count: dict = {}
    with_img = 0
    with_price = 0

    for p in raw:
        t = p.get("property_type", "desconocido")
        types[t] = types.get(t, 0) + 1
        lt = p.get("listing_type", "?")
        lt_count[lt] = lt_count.get(lt, 0) + 1
        if p.get("image_url"):
            with_img += 1
        if p.get("price"):
            with_price += 1

    print(f"Propiedades scrapeadas: {len(raw)}")
    print(f"  Con imagen: {with_img}/{len(raw)}")
    print(f"  Con precio: {with_price}/{len(raw)}")
    print(f"  Por tipo:   {types}")
    print(f"  Por listing: {lt_count}")
    print()

    # Insertar en BD
    async with AsyncSessionLocal() as db:
        n = await agent_a1.run_scraping(db)

        # Total en BD
        total = (await db.execute(
            select(func.count()).select_from(Property).where(Property.is_active == True)
        )).scalar()

    print(f"Nuevas insertadas en BD: {n}")
    print(f"Total propiedades en BD: {total}")

    if n == 0:
        print("\n(Ya estaban todas en BD o scraping no obtuvo nuevas)")

    print("\nListo. Ejecuta el servidor y ve a /properties para verlas.")


asyncio.run(main())
