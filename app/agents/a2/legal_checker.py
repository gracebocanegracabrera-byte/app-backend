from typing import Optional


class LegalChecker:

    def evaluate(self, price: Optional[float], ref_price: Optional[float] = None,
                 source_name: Optional[str] = None, source_url: Optional[str] = None,
                 area_m2: Optional[float] = None, district: Optional[str] = None,
                 price_diff_pct: Optional[float] = None) -> dict:

        risks = []
        score = 100

        if price_diff_pct is not None:
            if price_diff_pct < -40:
                risks.append("Precio significativamente por debajo del mercado (posible error o problema legal)")
                score -= 35
            elif price_diff_pct < -25:
                risks.append("Precio notablemente bajo — se recomienda verificar estado legal del inmueble")
                score -= 20
            elif price_diff_pct > 30:
                risks.append("Precio por encima del mercado — verificar valorización con tasador independiente")
                score -= 10

        if not area_m2:
            risks.append("Área no especificada — verificar con el vendedor")
            score -= 15

        if not district:
            risks.append("Ubicación exacta no especificada")
            score -= 10

        if source_name == "mock" or not source_url or source_url.startswith("mock://"):
            risks.append("Datos de demostración — verificar información en portales oficiales")
            score -= 5

        if not price:
            risks.append("Precio no publicado — requiere contacto con vendedor")
            score -= 15

        score = max(0, score)
        if score >= 75:
            status = "green"
        elif score >= 50:
            status = "yellow"
        else:
            status = "red"

        positive_notes = []
        if score >= 80:
            positive_notes.append("Datos completos y precio dentro del rango esperado")
        if source_name and source_name != "mock":
            positive_notes.append(f"Publicación verificada en {source_name}")

        return {
            "status": status,
            "score": score,
            "risks": risks,
            "positive_notes": positive_notes,
            "disclaimer": "Evaluación heurística académica — no reemplaza consulta legal ni SUNARP"
        }


legal_checker = LegalChecker()
