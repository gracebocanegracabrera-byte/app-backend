from typing import Optional

WEIGHT_PRICE = 0.35
WEIGHT_LEGAL = 0.30
WEIGHT_PROFILE_MATCH = 0.25
WEIGHT_DATA = 0.10


class ScoringEngine:

    def score_price(self, price_diff_pct: Optional[float]) -> float:
        if price_diff_pct is None:
            return 50.0
        if price_diff_pct <= -30:
            return 100.0
        elif price_diff_pct <= -15:
            return 85.0
        elif price_diff_pct <= 0:
            return 70.0
        elif price_diff_pct <= 15:
            return 55.0
        elif price_diff_pct <= 30:
            return 35.0
        else:
            return 20.0

    def score_legal(self, legal_status: Optional[str]) -> float:
        return {"green": 100.0, "yellow": 60.0, "red": 20.0}.get(legal_status or "", 50.0)

    def score_profile_match(self, property: dict, profile_prefs: dict) -> float:
        if not profile_prefs:
            return 50.0

        matches = 0
        total_criteria = 0

        if profile_prefs.get("property_type") and property.get("property_type"):
            total_criteria += 1
            if profile_prefs["property_type"].lower() in (property["property_type"] or "").lower():
                matches += 1

        if profile_prefs.get("zone") and property.get("district"):
            total_criteria += 1
            if profile_prefs["zone"].lower() in (property["district"] or "").lower():
                matches += 1

        if profile_prefs.get("price_max") and property.get("price"):
            total_criteria += 1
            if property["price"] <= profile_prefs["price_max"] * 1.1:
                matches += 1

        if profile_prefs.get("bedrooms") and property.get("bedrooms"):
            total_criteria += 1
            if abs(float(property["bedrooms"]) - float(profile_prefs["bedrooms"])) <= 1:
                matches += 1

        if total_criteria == 0:
            return 50.0
        return (matches / total_criteria) * 100

    def score_data_completeness(self, property: dict) -> float:
        fields = ["price", "area_m2", "district", "property_type", "bedrooms"]
        filled = sum(1 for f in fields if property.get(f) is not None)
        return (filled / len(fields)) * 100

    def calculate_score(self, property: dict, profile_prefs: dict,
                        price_diff_pct: Optional[float] = None,
                        legal_status: Optional[str] = None) -> dict:
        s_price = self.score_price(price_diff_pct)
        s_legal = self.score_legal(legal_status)
        s_profile = self.score_profile_match(property, profile_prefs)
        s_data = self.score_data_completeness(property)

        final_score = (
            s_price * WEIGHT_PRICE +
            s_legal * WEIGHT_LEGAL +
            s_profile * WEIGHT_PROFILE_MATCH +
            s_data * WEIGHT_DATA
        )

        if final_score >= 80:
            tag = "Oportunidad destacada"
        elif final_score >= 60:
            tag = "Buena opción"
        else:
            tag = "Revisar"

        return {
            "total_score": round(final_score, 1),
            "tag": tag,
            "breakdown": {
                "precio": round(s_price, 1),
                "legal": round(s_legal, 1),
                "match_perfil": round(s_profile, 1),
                "datos": round(s_data, 1),
            }
        }


scoring_engine = ScoringEngine()
