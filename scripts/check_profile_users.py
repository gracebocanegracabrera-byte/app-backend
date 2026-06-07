import asyncio, sys
sys.path.insert(0, '.')

async def main():
    from app.core.database import AsyncSessionLocal
    from app.models.user import User
    from app.models.profile import UserProfile
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        users = (await db.execute(select(User))).scalars().all()
        profiles = (await db.execute(select(UserProfile))).scalars().all()

        print("=== USERS ===")
        for u in users:
            print(f"  {u.email} | {u.id} | role={u.role}")

        print()
        print("=== PROFILES ===")
        for p in profiles:
            zone = p.preferences.get("zone") if p.preferences else "?"
            ptype = p.preferences.get("property_type") if p.preferences else "?"
            user = next((u for u in users if str(u.id) == str(p.user_id)), None)
            owner = user.email if user else "NO EXISTE EN DB !!!"
            print(f"  user_id={p.user_id}")
            print(f"    zone={zone}, type={ptype}")
            print(f"    -> pertenece a: {owner}")

asyncio.run(main())
