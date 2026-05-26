"""Tests for status mapping."""
from src.parsers.status import WALLAPOP_TO_INTERNAL, SHIPPED_STATES, RETURN_STATES


class TestStatusMapping:
    def test_all_mapped_statuses_are_strings(self):
        for walla, internal in WALLAPOP_TO_INTERNAL.items():
            assert isinstance(walla, str)
            assert isinstance(internal, str)

    def test_transaction_created_maps_to_por_enviar(self):
        assert WALLAPOP_TO_INTERNAL["TRANSACTION_CREATED"] == "POR_ENVIAR"

    def test_in_transit_maps_to_enviado(self):
        assert WALLAPOP_TO_INTERNAL["IN_TRANSIT"] == "ENVIADO"

    def test_delivered_maps_to_entregado(self):
        assert WALLAPOP_TO_INTERNAL["DELIVERED"] == "ENTREGADO"

    def test_dispute_maps_to_incidencia(self):
        assert WALLAPOP_TO_INTERNAL["DISPUTE_OPEN"] == "INCIDENCIA"
        assert WALLAPOP_TO_INTERNAL["DISPUTE_ESCALATED"] == "INCIDENCIA"

    def test_return_maps_to_en_devolucion(self):
        assert WALLAPOP_TO_INTERNAL["RETURN_IN_PROGRESS"] == "EN_DEVOLUCION"

    def test_cancelled(self):
        assert WALLAPOP_TO_INTERNAL["CANCELLED"] == "CANCELADO"

    def test_shipped_states_set(self):
        assert "ENVIADO" in SHIPPED_STATES
        assert "ENTREGADO" in SHIPPED_STATES
        assert "COMPLETADO" in SHIPPED_STATES

    def test_return_states_set(self):
        assert "EN_DEVOLUCION" in RETURN_STATES
        assert "INCIDENCIA" in RETURN_STATES

    def test_coverage(self):
        assert len(WALLAPOP_TO_INTERNAL) >= 18
