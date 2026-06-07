"""Elimina todos los registros de kpi_snapshots de la BD.

Ejecutar:
  cd app/backend
  venv\\Scripts\\activate
  python scripts/delete_kpi_snapshots.py
"""

import asyncio
import sys

sys.path.insert(0, ".")


async def main():
    from app.core.database import AsyncSessionLocal
    from app.models.kpi import KpiSnapshot
    from sqlalchemy import select, delete, func

    async with AsyncSessionLocal() as db:
        total = (await db.execute(
            select(func.count()).select_from(KpiSnapshot)
        )).scalar()

        print(f"Snapshots en BD: {total}")

        if total == 0:
            print("BD ya limpia.")
            return

        confirmar = input(f"Eliminar {total} snapshots? [s/N]: ").strip().lower()
        if confirmar != "s":
            print("Cancelado.")
            return

        await db.execute(delete(KpiSnapshot))
        await db.commit()

        print(f"Eliminados: {total} snapshots.")
        print("El worker ahora solo guardara snapshots con valor > 0 y cuando haya cambio.")


asyncio.run(main())
