"""Crea usuarios de prueba: admin, advisor y buyer.

Ejecutar:
  cd app/backend
  python scripts/seed_users.py

Credenciales creadas:
  admin@test.com   / Admin1234   (rol: admin)
  advisor@test.com / Advisor1234 (rol: advisor)
  buyer@test.com   / Buyer1234   (rol: buyer)
"""

import asyncio
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.core.auth import hash_password
from app.models.user import User

USERS = [
    {
        "email": "admin@test.com",
        "name": "Administrador",
        "password": "Admin1234",
        "role": "admin",
    },
    {
        "email": "advisor@test.com",
        "name": "Asesor Inmobiliario",
        "password": "Advisor1234",
        "role": "advisor",
    },
    {
        "email": "buyer@test.com",
        "name": "Comprador Demo",
        "password": "Buyer1234",
        "role": "buyer",
    },
]


async def main():
    async with AsyncSessionLocal() as db:
        created = 0
        skipped = 0

        for u in USERS:
            existing = (
                await db.execute(select(User).where(User.email == u["email"]))
            ).scalar_one_or_none()

            if existing:
                print(f"  [skip]  {u['email']} — ya existe (rol: {existing.role})")
                skipped += 1
                continue

            user = User(
                email=u["email"],
                name=u["name"],
                hashed_password=hash_password(u["password"]),
                role=u["role"],
                legal_disclaimer_accepted=True,
                legal_disclaimer_accepted_at=datetime.now(timezone.utc),
                privacy_policy_accepted=True,
                privacy_policy_version="1.0",
                data_processing_consent=True,
            )
            db.add(user)
            print(f"  [crear] {u['email']} — rol: {u['role']}  pass: {u['password']}")
            created += 1

        await db.commit()

    print(f"\nSeed usuarios: {created} creados, {skipped} omitidos.")
    print("\nCredenciales:")
    print("  admin@test.com    / Admin1234    -> /dashboard, /crm")
    print("  advisor@test.com  / Advisor1234  -> /chat, /crm")
    print("  buyer@test.com    / Buyer1234    -> /chat, /properties, /ranking")


asyncio.run(main())
