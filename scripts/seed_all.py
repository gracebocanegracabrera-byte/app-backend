"""Seed completo de demo: usuarios + propiedades + perfil + evaluaciones + ranking + CRM.

Ejecutar (DESPUÉS de `alembic upgrade head`):
  cd app/backend
  python scripts/seed_all.py

Idempotente: re-ejecutar no duplica usuarios, perfil, evaluaciones, leads ni citas.

Credenciales creadas (todas password: demo1234):
  admin@demo.com   (rol: admin)
  advisor@demo.com (rol: advisor)
  buyer@demo.com   (rol: buyer)
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.auth import hash_password
from app.models.user import User
from app.models.profile import UserProfile
from app.models.property import Property
from app.models.crm import Lead, Appointment
from app.agents.a1.service import agent_a1
from app.agents.a2.service import agent_a2
from app.agents.a4.service import agent_a4
from app.agents.a5.service import agent_a5

DEMO_USERS = [
    {"email": "admin@demo.com", "name": "Admin Demo", "password": "demo1234", "role": "admin"},
    {"email": "advisor@demo.com", "name": "Asesor Demo", "password": "demo1234", "role": "advisor"},
    {"email": "buyer@demo.com", "name": "Comprador Demo", "password": "demo1234", "role": "buyer"},
]

BUYER_PROFILE = {
    "zone": "Miraflores",
    "price_min": 200000,
    "price_max": 400000,
    "property_type": "departamento",
    "bedrooms": 2,
    "area_m2_min": 60,
    "purpose": "compra",
}

LEAD_STATUSES = ["scheduled", "interested", "contacted"]


async def seed_users(db) -> dict:
    created_users = {}
    created, skipped = 0, 0
    for ud in DEMO_USERS:
        existing = (
            await db.execute(select(User).where(User.email == ud["email"]))
        ).scalar_one_or_none()
        if existing:
            created_users[ud["role"]] = existing
            print(f"  [skip]  {ud['email']} — ya existe (rol: {existing.role})")
            skipped += 1
            continue

        user = User(
            email=ud["email"],
            name=ud["name"],
            hashed_password=hash_password(ud["password"]),
            role=ud["role"],
            # Consentimiento aceptado para usuarios demo — Ley N° 29733
            legal_disclaimer_accepted=True,
            legal_disclaimer_accepted_at=datetime.now(timezone.utc),
            privacy_policy_accepted=True,
            privacy_policy_version="1.0",
            data_processing_consent=True,
        )
        db.add(user)
        await db.flush()
        created_users[ud["role"]] = user
        print(f"  [crear] {ud['email']} — rol: {ud['role']}")
        created += 1

    await db.commit()
    print(f"  -> usuarios: {created} creados, {skipped} omitidos")
    return created_users


async def seed_buyer_profile(db, buyer: User) -> UserProfile:
    existing = (
        await db.execute(select(UserProfile).where(UserProfile.user_id == buyer.id))
    ).scalar_one_or_none()
    if existing:
        print("  [skip]  perfil buyer ya existe")
        return existing

    profile = UserProfile(user_id=buyer.id, preferences=BUYER_PROFILE, completeness_pct=100.0)
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    print("  [crear] perfil buyer (Miraflores, departamento, 2 hab, S/200k-400k)")
    return profile


async def seed_evaluations(db, buyer: User, props: list[Property]) -> int:
    count = 0
    for prop in props:
        try:
            await agent_a2.evaluate_property(prop.id, buyer.id, db)
            count += 1
        except Exception as e:
            print(f"  [warn]  evaluación fallida para {prop.id}: {e}")
    print(f"  -> evaluaciones: {count}/{len(props)}")
    return count


async def seed_ranking(db, buyer: User) -> int:
    try:
        ranking = await agent_a4.generate_ranking(buyer.id, db, limit=20)
        print(f"  -> ranking: {len(ranking)} posiciones")
        return len(ranking)
    except Exception as e:
        print(f"  [warn]  ranking fallido: {e}")
        return 0


async def seed_crm(db, buyer: User, props: list[Property]) -> tuple[int, int]:
    leads_count, appt_count = 0, 0
    for i, prop in enumerate(props[:3]):
        try:
            lead = await agent_a5.create_lead(buyer.id, prop.id, db)
            lead.status = LEAD_STATUSES[i]
            leads_count += 1

            if i == 0:
                existing_appt = (
                    await db.execute(select(Appointment).where(Appointment.lead_id == lead.id))
                ).scalar_one_or_none()
                if not existing_appt:
                    appt = Appointment(
                        lead_id=lead.id,
                        property_id=prop.id,
                        scheduled_at=datetime.now(timezone.utc) + timedelta(days=3),
                        status="confirmed",
                    )
                    db.add(appt)
                    appt_count += 1
        except Exception as e:
            print(f"  [warn]  lead/cita fallido para {prop.id}: {e}")

    await db.commit()
    print(f"  -> leads: {leads_count}, citas: {appt_count}")
    return leads_count, appt_count


async def seed():
    async with AsyncSessionLocal() as db:
        print("Seed demo — iniciando\n")

        print("1. Usuarios")
        users = await seed_users(db)

        print("\n2. Propiedades")
        n_props = await agent_a1.run_scraping(db)
        print(f"  -> propiedades nuevas importadas: {n_props}")

        buyer = users["buyer"]

        print("\n3. Perfil buyer")
        await seed_buyer_profile(db, buyer)

        props_result = await db.execute(
            select(Property).where(Property.is_active == True).limit(10)
        )
        props = props_result.scalars().all()
        if len(props) < 1:
            print("\n[error] No hay propiedades activas en BD — abortando pasos 4-6.")
            return

        print("\n4. Evaluaciones (primeras 10 propiedades)")
        eval_count = await seed_evaluations(db, buyer, props)

        print("\n5. Ranking")
        ranking_count = await seed_ranking(db, buyer)

        print("\n6. CRM (leads + cita demo)")
        leads_count, appt_count = await seed_crm(db, buyer, props)

        total_props = (
            await db.execute(select(Property).where(Property.is_active == True))
        ).scalars().all()

        print("\n" + "=" * 50)
        print("RESUMEN DEL SEED")
        print("=" * 50)
        print(f"  Usuarios demo:     {len(users)}")
        print(f"  Propiedades:       {len(total_props)} activas (>=30 requerido)")
        print(f"  Perfil buyer:      generado")
        print(f"  Evaluaciones:      {eval_count}")
        print(f"  Ranking:           {ranking_count} posiciones")
        print(f"  Leads:             {leads_count}")
        print(f"  Citas:             {appt_count}")
        print("\nCredenciales de acceso (password: demo1234):")
        for ud in DEMO_USERS:
            print(f"  {ud['role']:8s} -> {ud['email']}")
        print("\nSeed completado.")


if __name__ == "__main__":
    asyncio.run(seed())
