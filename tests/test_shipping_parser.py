"""Tests for shipping details parsing."""
import pytest
from src.parsers.shipping import (
    detect_carrier, extract_deadline, extract_tracking_code,
    extract_label_url, process_label_url, extract_shipping_details,
)


class TestCarrierDetection:
    def test_inpost_from_icon(self):
        info = [{"icon": {"url": "https://cdn.wallapop.com/InPost_logo.png"}}]
        assert detect_carrier(info, {}) == "InPost"

    def test_correos_from_icon(self):
        info = [{"icon": {"url": "https://cdn.wallapop.com/Correos_icon.svg"}}]
        assert detect_carrier(info, {}) == "Correos"

    def test_seur_from_description(self):
        status = {"description": "Entrégalo en tu punto Seur más cercano"}
        assert detect_carrier([], status) == "Seur"

    def test_no_carrier(self):
        assert detect_carrier([], {}) is None

    def test_icon_takes_priority(self):
        info = [{"icon": {"url": "https://cdn/InPost.png"}}]
        status = {"description": "Use Correos"}
        assert detect_carrier(info, status) == "InPost"


class TestDeadlineExtraction:
    def test_bold_deadline(self):
        status = {"description": 'entrégalo antes de que finalice el: <b>Viernes, 23 Mayo 2025</b>'}
        assert extract_deadline(status) == "Viernes, 23 Mayo 2025"

    def test_strong_deadline(self):
        status = {"description": 'antes del <strong>Monday, 5 January 2026</strong>'}
        assert extract_deadline(status) == "Monday, 5 January 2026"

    def test_no_deadline(self):
        assert extract_deadline({"description": "no date here"}) is None

    def test_empty(self):
        assert extract_deadline({}) is None
        assert extract_deadline(None) is None


class TestTrackingCode:
    def test_from_banner(self):
        info = [{"action": {"payload": {"banner": {"tracking_code": "TR123456"}}}}]
        assert extract_tracking_code(info) == "TR123456"

    def test_from_html(self):
        info = [{"description": "Código: <strong>PK-ABC-789</strong>", "action": {"payload": {}}}]
        assert extract_tracking_code(info) == "PK-ABC-789"

    def test_no_code(self):
        assert extract_tracking_code([]) is None
        assert extract_tracking_code(None) is None


class TestLabelURL:
    def test_tracking_label(self):
        url = "wallapop://trackinglabel?url=https://real-label.pdf"
        assert process_label_url(url) == "https://real-label.pdf"

    def test_barcode(self):
        url = "wallapop://delivery/barcode?b=12345"
        assert process_label_url(url) == "https://es.wallapop.com/app/delivery/tracking/barcode/12345"

    def test_passthrough(self):
        url = "https://normal-url.com/label.pdf"
        assert process_label_url(url) == url


class TestFullShippingDetails:
    def test_combines_all(self):
        details = {
            "shipping_status": {
                "description": 'Antes del <b>Viernes, 23 Mayo 2025</b>',
                "actions": [
                    {"title": "Mostrar etiqueta", "action": {"payload": {"link_url": "wallapop://trackinglabel?url=https://label.pdf"}}}
                ],
            },
            "transaction_status_info": [
                {"icon": {"url": "https://cdn/InPost.png"}, "action": {"payload": {"banner": {"tracking_code": "TR999"}}}}
            ],
            "analytics": {"buyer_country": "ES"},
        }
        result = extract_shipping_details(details, "req-123", "TRANSACTION_CREATED")
        assert result["carrier"] == "InPost"
        assert result["tracking_code"] == "TR999"
        assert result["deadline"] == "Viernes, 23 Mayo 2025"
        assert result["label_url"] == "https://label.pdf"
        assert result["buyer_country"] == "ES"

    def test_deadline_only_for_transaction_created(self):
        details = {
            "shipping_status": {"description": '<b>Viernes, 23 Mayo 2025</b>'},
            "transaction_status_info": [],
        }
        result = extract_shipping_details(details, "req-1", "IN_TRANSIT")
        assert result["deadline"] is None
