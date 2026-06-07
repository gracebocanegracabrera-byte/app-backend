from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.models.evaluation import Evaluation
from app.models.ranking import Ranking
from app.agents.a4.scoring import scoring_engine
from app.agents.a1.service import agent_a1


class AgentA4Service:

    async def generate_ranking(self, user_id: UUID, db: AsyncSession, limit: int = 20) -> list[dict]:
        from app.models.profile import UserProfile
        profile_result = await db.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        profile = profile_result.scalar_one_or_none()
        prefs = profile.preferences if profile else {}

        # Usar el mismo filtro de perfil que A1 — ranking solo sobre props visibles al usuario
        properties, _total, _relaxed, _suggestion = await agent_a1.get_filtered_properties(
            db, user_id=user_id, limit=100
        )

        evals_result = await db.execute(
            select(Evaluation).where(Evaluation.user_id == user_id)
        )
        evaluations = {str(e.property_id): e for e in evals_result.scalars().all()}

        scored = []
        for prop in properties:
            eval_ = evaluations.get(str(prop.id))
            price_diff = None
            legal_status = None

            if eval_ and eval_.report:
                price_diff = eval_.report.get("price_analysis", {}).get("price_diff_pct")
                legal_status = eval_.legal_status

            prop_dict = {
                "id": str(prop.id),
                "title": prop.title,
                "price": prop.price,
                "area_m2": prop.area_m2,
                "district": prop.district,
                "zone": prop.zone,
                "property_type": prop.property_type,
                "bedrooms": prop.bedrooms,
            }

            score_result = scoring_engine.calculate_score(prop_dict, prefs, price_diff, legal_status)
            scored.append({
                "property": prop_dict,
                "score": score_result["total_score"],
                "tag": score_result["tag"],
                "breakdown": score_result["breakdown"],
                "has_evaluation": eval_ is not None,
                "legal_status": legal_status,
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        top = scored[:limit]

        await self._save_ranking(user_id, top, db)

        return top

    async def _save_ranking(self, user_id: UUID, ranked: list[dict], db: AsyncSession):
        await db.execute(delete(Ranking).where(Ranking.user_id == user_id))

        for i, item in enumerate(ranked):
            db.add(Ranking(
                user_id=user_id,
                property_id=UUID(item["property"]["id"]),
                score=item["score"],
                rank_position=i + 1,
            ))

        await db.commit()


agent_a4 = AgentA4Service()
