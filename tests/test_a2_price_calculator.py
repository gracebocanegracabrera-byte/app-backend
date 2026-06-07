import pytest
from app.agents.a2.price_calculator import PriceCalculator, PRICE_PER_M2_PEN


@pytest.fixture
def calc():
    return PriceCalculator()


class TestGetRefPriceM2:

    def test_known_district(self, calc):
        price, key = calc.get_ref_price_m2("Victor Larco", None)
        assert price == 3500
        assert key == "victor larco"

    def test_partial_match_district(self, calc):
        price, key = calc.get_ref_price_m2("Huanchaco", None)
        assert price == 2800

    def test_zone_fallback(self, calc):
        price, key = calc.get_ref_price_m2(None, "Zona Golf")
        assert price == 3500

    def test_unknown_district_default(self, calc):
        price, key = calc.get_ref_price_m2("Desconocido", None)
        assert price == 2000
        assert key == "promedio Trujillo"

    def test_none_district_and_zone(self, calc):
        price, key = calc.get_ref_price_m2(None, None)
        assert price == 2000

    def test_case_insensitive(self, calc):
        price, key = calc.get_ref_price_m2("VICTOR LARCO", None)
        assert price == 3500

    def test_substring_match(self, calc):
        price, key = calc.get_ref_price_m2("Urb. Victor Larco", None)
        assert price == 3500


class TestCalculate:

    def test_full_data_victor_larco(self, calc):
        result = calc.calculate(350000, 100, "Victor Larco", None)
        assert result["ref_price_per_m2"] == 3500
        assert result["ref_total_price"] == 350000
        assert result["area_used"] == 100
        assert result["estimated"] is False
        assert result["location_used"] == "victor larco"

    def test_price_above_market(self, calc):
        result = calc.calculate(500000, 100, "Victor Larco", None)
        assert result["price_diff_pct"] > 15
        assert result["verdict"] == "Por encima del mercado"

    def test_price_below_market(self, calc):
        result = calc.calculate(200000, 100, "Victor Larco", None)
        assert result["price_diff_pct"] < -15
        assert result["verdict"] == "Por debajo del mercado"

    def test_price_in_line(self, calc):
        result = calc.calculate(340000, 100, "Victor Larco", None)
        assert -15 <= result["price_diff_pct"] <= 15
        assert result["verdict"] == "En línea con el mercado"

    def test_no_price_returns_no_verdict(self, calc):
        result = calc.calculate(None, 100, "Victor Larco", None)
        assert result["price_diff_pct"] is None
        assert result["verdict"] == "Sin precio para comparar"

    def test_no_area_uses_estimate(self, calc):
        result = calc.calculate(350000, None, "Victor Larco", None)
        assert result["area_used"] == 70
        assert result["estimated"] is True

    def test_unknown_district_uses_default(self, calc):
        result = calc.calculate(200000, 100, "Unknown", None)
        assert result["ref_price_per_m2"] == 2000
        assert result["location_used"] == "promedio Trujillo"

    def test_zone_based_calculation(self, calc):
        result = calc.calculate(400000, 120, None, "Zona Golf")
        assert result["ref_price_per_m2"] == 3500
        assert result["location_used"] == "victor larco"


class TestPriceTable:

    def test_min_10_districts(self):
        districts = [k for k in PRICE_PER_M2_PEN if k != "default"]
        assert len(districts) >= 10

    def test_all_prices_positive(self):
        for price in PRICE_PER_M2_PEN.values():
            assert price > 0
