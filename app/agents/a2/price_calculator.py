from typing import Optional

PRICE_PER_M2_PEN = {
    "victor larco": 3500,
    "trujillo": 3000,
    "huanchaco": 2800,
    "buenos aires": 1800,
    "moche": 1400,
    "salaverry": 1600,
    "la esperanza": 1200,
    "el porvenir": 1000,
    "florencia de mora": 1100,
    "laredo": 1300,
    "default": 2000,
}

ZONE_DISTRICT_MAP = {
    "zona golf": "victor larco",
    "zona residencial": "victor larco",
    "trujillo centro": "trujillo",
    "zona playera": "huanchaco",
    "zona sur": "moche",
    "zona norte": "la esperanza",
    "zona este": "el porvenir",
}

class PriceCalculator:

    def get_ref_price_m2(self, district: Optional[str], zone: Optional[str]) -> tuple[float, str]:
        raw = (district or zone or "").lower().strip()
        if not raw:
            return PRICE_PER_M2_PEN["default"], "promedio Trujillo"
        for key, price in PRICE_PER_M2_PEN.items():
            if key in raw or raw in key:
                return price, key
        zone_key = (zone or "").lower().strip()
        mapped = ZONE_DISTRICT_MAP.get(zone_key)
        if mapped and mapped in PRICE_PER_M2_PEN:
            return PRICE_PER_M2_PEN[mapped], mapped
        return PRICE_PER_M2_PEN["default"], "promedio Trujillo"

    def calculate(self, price: Optional[float], area_m2: Optional[float],
                  district: Optional[str], zone: Optional[str]) -> dict:
        ref_m2, location_key = self.get_ref_price_m2(district, zone)
        estimated = False
        if not area_m2:
            area_m2 = 70
            estimated = True
        ref_total = ref_m2 * area_m2
        result = {
            "ref_price_per_m2": ref_m2,
            "ref_total_price": round(ref_total, 2),
            "location_used": location_key,
            "area_used": area_m2,
            "estimated": estimated,
            "price_diff_pct": None,
            "verdict": "Sin precio para comparar",
        }
        if price:
            diff_pct = ((price - ref_total) / ref_total) * 100
            result["price_diff_pct"] = round(diff_pct, 1)
            if diff_pct < -15:
                result["verdict"] = "Por debajo del mercado"
            elif diff_pct > 15:
                result["verdict"] = "Por encima del mercado"
            else:
                result["verdict"] = "En línea con el mercado"
        return result

price_calculator = PriceCalculator()
