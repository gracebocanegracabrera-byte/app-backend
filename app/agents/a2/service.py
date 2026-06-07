from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.property import Property
from app.models.evaluation import Evaluation
from app.agents.a2.price_calculator import price_calculator
from app.agents.a2.legal_checker import legal_checker


STATUS_LABELS = {"green": "sin alertas legales", "yellow": "con alertas menores", "red": "con alertas importantes"}


class AgentA2Service:

    async def evaluate_property(self, property_id: UUID, user_id: UUID, db: AsyncSession) -> Evaluation:
        existing = await db.execute(
            select(Evaluation).where(
                Evaluation.property_id == property_id,
                Evaluation.user_id == user_id
            )
        )
        eval_existing = existing.scalar_one_or_none()
        if eval_existing:
            return eval_existing

        prop_result = await db.execute(select(Property).where(Property.id == property_id))
        prop = prop_result.scalar_one_or_none()
        if not prop:
            raise ValueError("Propiedad no encontrada")

        price_analysis = price_calculator.calculate(
            prop.price, prop.area_m2, prop.district, prop.zone
        )

        legal_analysis = legal_checker.evaluate(
            price=prop.price,
            source_name=prop.source_name, source_url=prop.source_url,
            area_m2=prop.area_m2, district=prop.district,
            price_diff_pct=price_analysis.get("price_diff_pct")
        )

        score = legal_analysis["score"]
        risk_level = "low" if score >= 75 else ("medium" if score >= 50 else "high")

        report = {
            "property": {
                "id": str(prop.id),
                "title": prop.title,
                "price": prop.price,
                "area_m2": prop.area_m2,
                "district": prop.district,
            },
            "price_analysis": price_analysis,
            "legal_analysis": legal_analysis,
            "summary": self._build_summary(price_analysis, legal_analysis)
        }

        evaluation = Evaluation(
            property_id=property_id,
            user_id=user_id,
            ref_price=price_analysis.get("ref_total_price"),
            legal_status=legal_analysis["status"],
            risk_level=risk_level,
            report=report
        )
        db.add(evaluation)
        await db.commit()
        await db.refresh(evaluation)
        return evaluation

    def _build_summary(self, price_analysis: dict, legal_analysis: dict) -> str:
        verdict = price_analysis.get("verdict", "")
        status = legal_analysis.get("status", "")
        status_text = STATUS_LABELS.get(status, "")
        return f"Propiedad {verdict.lower()}, {status_text}."


agent_a2 = AgentA2Service()
