"""Elimina propiedades ficticias/mock de la BD.

Criterio: source_name != 'infocasas' O source_url no empieza con infocasas.com.pe
Solo conserva propiedades reales scrapeadas de InfoCasas.com.pe

Ejecutar:
  cd app/backend
  venv\\Scripts\\activate
  python scripts/delete_mock_properties.py
"""

import asyncio
import sys

sys.path.insert(0, ".")


async def main():
    from app.core.database import AsyncSessionLocal
    from app.models.property import Property
    from sqlalchemy import select, delete, func, and_, or_

    async with AsyncSessionLocal() as db:
        # Contar total antes
        total_antes = (await db.execute(
            select(func.count()).select_from(Property)
        )).scalar()

        # Identificar mocks: source_name != 'infocasas' o source_url no apunta a infocasas
        mock_filter = or_(
            Property.source_name != "infocasas",
            Property.source_name == None,
            ~Property.source_url.like("%infocasas.com.pe%"),
            Property.source_url == None,
        )

        # Contar cuantas son mock
        total_mock = (await db.execute(
            select(func.count()).select_from(Property).where(mock_filter)
        )).scalar()

        print(f"Total en BD:      {total_antes}")
        print(f"Propiedades mock: {total_mock}")
        print(f"Reales (a quedar): {total_antes - total_mock}")
        print()

        if total_mock == 0:
            print("No hay propiedades mock. BD ya limpia.")
            return

        # Previsualizar algunas
        sample = (await db.execute(
            select(Property.title, Property.source_name, Property.source_url)
            .where(mock_filter)
            .limit(5)
        )).fetchall()

        print("Muestra de las que se eliminarán:")
        for row in sample:
            print(f"  [{row[1] or 'NULL'}] {row[0][:60]}")
            print(f"    {row[2] or 'NULL'}")
        if total_mock > 5:
            print(f"  ... y {total_mock - 5} más")
        print()

        confirmar = input(f"¿Eliminar {total_mock} propiedades mock? [s/N]: ").strip().lower()
        if confirmar != "s":
            print("Cancelado.")
            return

        # Eliminar
        result = await db.execute(delete(Property).where(mock_filter))
        await db.commit()

        eliminadas = result.rowcount
        total_despues = (await db.execute(
            select(func.count()).select_from(Property)
        )).scalar()

        print(f"\nEliminadas: {eliminadas}")
        print(f"Quedan en BD: {total_despues} (todas reales de InfoCasas.com.pe)")


asyncio.run(main())
