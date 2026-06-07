import pytest
from app.agents.a2.legal_checker import LegalChecker


@pytest.fixture
def checker():
    return LegalChecker()


class TestGreenStatus:

    def test_green_all_good_data(self, checker):
        result = checker.evaluate(
            price=300000, ref_price=280000, source_name="urbania",
            source_url="https://urbania.pe/propiedad/123", area_m2=100,
            district="victor larco", price_diff_pct=7.1
        )
        assert result["status"] == "green"
        assert result["score"] >= 75

    def test_green_in_line_with_market(self, checker):
        result = checker.evaluate(
            price=200000, ref_price=210000, source_name="adondevivir",
            source_url="https://adondevivir.pe/prop/1", area_m2=80,
            district="trujillo", price_diff_pct=-4.8
        )
        assert result["status"] == "green"
        assert result["score"] == 100

    def test_green_has_positive_notes(self, checker):
        result = checker.evaluate(
            price=200000, ref_price=210000, source_name="urbania",
            source_url="https://urbania.pe/p/1", area_m2=80,
            district="trujillo", price_diff_pct=-4.8
        )
        assert len(result["positive_notes"]) >= 1
        assert any("urbania" in n for n in result["positive_notes"])

    def test_green_score_boundary(self, checker):
        result = checker.evaluate(
            price=150000, ref_price=200000, source_name="mock",
            source_url="mock://test", area_m2=50,
            district="huanchaco", price_diff_pct=-26
        )
        assert result["score"] >= 75
        assert result["status"] == "green"


class TestYellowStatus:

    def test_yellow_notably_low_price_and_missing_area(self, checker):
        result = checker.evaluate(
            price=140000, ref_price=200000, source_name="urbania",
            source_url="https://urbania.pe/p/1", area_m2=None,
            district="trujillo", price_diff_pct=-30
        )
        assert result["status"] == "yellow"
        assert result["score"] < 75
        assert any("notablemente" in r.lower() for r in result["risks"])
        assert any("área no especificada" in r.lower() for r in result["risks"])

    def test_yellow_above_market_and_missing_data(self, checker):
        result = checker.evaluate(
            price=300000, ref_price=200000, source_name="urbania",
            source_url="https://urbania.pe/p/1", area_m2=None,
            district=None, price_diff_pct=35
        )
        assert result["status"] == "yellow"
        assert result["score"] < 75
        assert any("encima del mercado" in r.lower() for r in result["risks"])

    def test_yellow_incomplete_data_mock_source(self, checker):
        result = checker.evaluate(
            price=140000, ref_price=200000, source_name="mock",
            source_url="mock://test", area_m2=None,
            district="trujillo", price_diff_pct=-30
        )
        assert result["status"] == "yellow"
        assert any("área no especificada" in r.lower() for r in result["risks"])
        assert any("demostración" in r.lower() for r in result["risks"])

    def test_yellow_missing_area_district_and_no_price(self, checker):
        result = checker.evaluate(
            price=None, ref_price=210000, source_name="urbania",
            source_url="https://urbania.pe/p/1", area_m2=None,
            district=None, price_diff_pct=None
        )
        assert result["status"] == "yellow"
        assert any("área no especificada" in r.lower() for r in result["risks"])
        assert any("ubicación" in r.lower() for r in result["risks"])

    def test_yellow_score_boundary_50_74(self, checker):
        result = checker.evaluate(
            price=150000, ref_price=200000, source_name="mock",
            source_url="mock://test", area_m2=None,
            district=None, price_diff_pct=-30
        )
        assert 50 <= result["score"] < 75
        assert result["status"] == "yellow"


class TestRedStatus:

    def test_red_extremely_low_price_and_no_area(self, checker):
        result = checker.evaluate(
            price=None, ref_price=200000, source_name="urbania",
            source_url="https://urbania.pe/p/1", area_m2=None,
            district="trujillo", price_diff_pct=-60
        )
        assert result["status"] == "red"
        assert result["score"] < 50
        assert any("significativamente" in r.lower() for r in result["risks"])

    def test_red_no_basic_data_with_alert(self, checker):
        result = checker.evaluate(
            price=None, ref_price=None, source_name="mock",
            source_url=None, area_m2=None, district=None, price_diff_pct=-50
        )
        assert result["status"] == "red"
        assert result["score"] < 50
        assert any("precio no publicado" in r.lower() for r in result["risks"])
        assert any("demostración" in r.lower() for r in result["risks"])

    def test_red_no_basic_data_yellow_score(self, checker):
        result = checker.evaluate(
            price=None, ref_price=None, source_name="mock",
            source_url=None, area_m2=None, district=None, price_diff_pct=None
        )
        assert result["status"] == "yellow"
        assert result["score"] >= 50

    def test_red_score_below_50(self, checker):
        result = checker.evaluate(
            price=50000, ref_price=200000, source_name="mock",
            source_url="mock://fake", area_m2=None,
            district=None, price_diff_pct=-75
        )
        assert result["status"] == "red"
        assert result["score"] < 50


class TestEdgeCases:

    def test_no_price_diff_pct(self, checker):
        result = checker.evaluate(
            price=None, ref_price=None, source_name="urbania",
            source_url="https://urbania.pe/p/1", area_m2=80,
            district="trujillo", price_diff_pct=None
        )
        assert result["status"] == "green"
        assert result["score"] >= 75

    def test_mock_source_warning(self, checker):
        result = checker.evaluate(
            price=200000, ref_price=210000, source_name="mock",
            source_url="mock://test", area_m2=80,
            district="trujillo", price_diff_pct=-4.8
        )
        assert any("demostración" in r.lower() for r in result["risks"])

    def test_none_source_name(self, checker):
        result = checker.evaluate(
            price=200000, ref_price=210000, source_name=None,
            source_url="https://urbania.pe/p/1", area_m2=80,
            district="trujillo", price_diff_pct=-4.8
        )
        assert result["score"] >= 80

    def test_none_source_url(self, checker):
        result = checker.evaluate(
            price=200000, ref_price=210000, source_name="urbania",
            source_url=None, area_m2=80,
            district="trujillo", price_diff_pct=-4.8
        )
        assert any("demostración" in r.lower() for r in result["risks"])

    def test_price_above_30_pct_with_missing_area(self, checker):
        result = checker.evaluate(
            price=300000, ref_price=200000, source_name="urbania",
            source_url="https://urbania.pe/p/1", area_m2=None,
            district=None, price_diff_pct=40
        )
        assert result["status"] == "yellow"
        assert any("encima del mercado" in r.lower() for r in result["risks"])

    def test_disclaimer_present(self, checker):
        result = checker.evaluate(
            price=200000, ref_price=210000, source_name="urbania",
            source_url="https://urbania.pe/p/1", area_m2=80,
            district="trujillo", price_diff_pct=-4.8
        )
        assert "disclaimer" in result
        assert "SUNARP" in result["disclaimer"]

    def test_score_never_below_zero(self, checker):
        result = checker.evaluate(
            price=10000, ref_price=200000, source_name="mock",
            source_url="mock://fake", area_m2=None,
            district=None, price_diff_pct=-95
        )
        assert result["score"] >= 0
