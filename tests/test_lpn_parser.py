"""Tests for LPN parser — the core regex extraction logic."""
import pytest
from src.parsers.lpn import extract_lpns_from_description, extract_single_lpn, LPNResult


class TestFlexibleLPNExtraction:
    def test_amazon_fba_format(self):
        result = extract_lpns_from_description("Item: LPNWE324817902")
        assert result.lpns == ["LPNWE324817902"]
        assert result.num_products == 1

    def test_manual_format(self):
        result = extract_lpns_from_description("Producto LPN1SIKA3 en buen estado")
        assert result.lpns == ["LPN1SIKA3"]

    def test_alphanumeric_format(self):
        result = extract_lpns_from_description("Ref: LPNKARCHER1")
        assert result.lpns == ["LPNKARCHER1"]

    def test_with_location(self):
        result = extract_lpns_from_description("LPNWE324817902 - DER/099")
        assert result.lpns == ["LPNWE324817902"]
        assert result.locations == ["DER/099"]

    def test_multiple_lpns(self):
        desc = "LPNWE001 - IZQ/01\nLPNWE002 - DER/02\nLPNWE003"
        result = extract_lpns_from_description(desc)
        assert len(result.lpns) == 3
        assert result.locations[0] == "IZQ/01"
        assert result.locations[2] == ""

    def test_deduplicates(self):
        result = extract_lpns_from_description("LPNWE001 and again LPNWE001")
        assert result.lpns == ["LPNWE001"]

    def test_no_lpns(self):
        result = extract_lpns_from_description("No tracking codes here")
        assert result.lpns == []
        assert result.num_products == 0

    def test_empty_description(self):
        result = extract_lpns_from_description("")
        assert result.num_products == 0

    def test_none_description(self):
        result = extract_lpns_from_description(None)
        assert result.num_products == 0

    def test_short_codes_excluded(self):
        result = extract_lpns_from_description("LPNAB is too short")
        assert result.lpns == []


class TestPriceCalculation:
    def test_single_lpn_with_cost(self):
        result = extract_lpns_from_description("LPNWE001", cost_amount=50.0)
        assert result.prices == ["50.00"]

    def test_multiple_lpns_price_split(self):
        result = extract_lpns_from_description("LPNWE001\nLPNWE002", cost_amount=100.0)
        assert result.prices == ["50.00", "50.00"]

    def test_sale_price_fallback(self):
        result = extract_lpns_from_description("LPNWE001", sale_price=30.0)
        assert result.prices == ["30.00"]

    def test_cost_overrides_sale_price(self):
        result = extract_lpns_from_description("LPNWE001", cost_amount=40.0, sale_price=30.0)
        assert result.prices == ["40.00"]

    def test_no_price(self):
        result = extract_lpns_from_description("LPNWE001")
        assert result.prices == []


class TestCSVProperties:
    def test_lpns_csv(self):
        result = extract_lpns_from_description("LPNWE001\nLPNWE002")
        assert result.lpns_csv == "LPNWE001,LPNWE002"

    def test_empty_csv(self):
        result = extract_lpns_from_description("no lpns")
        assert result.lpns_csv == ""


class TestStrictLPN:
    def test_valid_strict(self):
        assert extract_single_lpn("Item LPNWE324817902") == "LPNWE324817902"

    def test_multiple_returns_none(self):
        assert extract_single_lpn("LPNWE001 and LPNWE002") is None

    def test_no_match(self):
        assert extract_single_lpn("No codes") is None

    def test_empty(self):
        assert extract_single_lpn("") is None

    def test_none(self):
        assert extract_single_lpn(None) is None
