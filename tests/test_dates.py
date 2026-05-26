"""Tests for bilingual date parsing."""
import pytest
from src.parsers.dates import convert_spanish_date_to_timestamp


class TestSpanishDateConversion:
    def test_spanish_date(self):
        ts = convert_spanish_date_to_timestamp("Viernes, 23 Mayo 2025")
        assert ts is not None
        assert isinstance(ts, int)

    def test_english_date(self):
        ts = convert_spanish_date_to_timestamp("Friday, 23 May 2025")
        assert ts is not None

    def test_both_parse_same(self):
        spa = convert_spanish_date_to_timestamp("Viernes, 23 Mayo 2025")
        eng = convert_spanish_date_to_timestamp("Friday, 23 May 2025")
        assert spa == eng

    def test_invalid_date(self):
        assert convert_spanish_date_to_timestamp("not a date") is None

    def test_empty(self):
        assert convert_spanish_date_to_timestamp("") is None

    def test_none(self):
        assert convert_spanish_date_to_timestamp(None) is None

    def test_partial_spanish(self):
        ts = convert_spanish_date_to_timestamp("Lunes, 1 Enero 2024")
        assert ts is not None
