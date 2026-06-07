"""Simula exactamente lo que hace el endpoint HTTP para buyer."""
import asyncio, sys
sys.path.insert(0, '.')

async def main():
    from app.core.database import AsyncSessionLocal
    from app.agents.a1.service import agent_a1
    from app.models.property import Property
    from app.models.profile import UserProfile
    from sqlalchemy import select, func, and_
    import uuid

    # buyer UUID
    buyer_id = uuid.UUID("c0b8d1f4-bcbd-405e-9746-f4ab6a0bcf4f")

    async with AsyncSessionLocal() as db:
        # 1. Simular get_current_user (carga usuario de DB)
        from app.models.user import User
        user_result = await db.execute(select(User).where(User.id == buyer_id))
        user = user_result.scalar_one_or_none()
        print(f"Usuario: {user.email if user else 'NOT FOUND'}")

        # 2. Llamar service exactamente como el endpoint
        try:
            items, total, sug = await agent_a1.get_filtered_properties(
                db,
                user_id=user.id,
                district=None,
                price_min=None,
                price_max=None,
                property_type=None,
                listing_type=None,
                page=1,
                limit=12,
            )
            print(f"Resultado: total={total}, items={len(items)}")
            print(f"Suggestion: {sug}")
        except Exception as e:
            import traceback
            print(f"ERROR: {e}")
            traceback.print_exc()

asyncio.run(main())
