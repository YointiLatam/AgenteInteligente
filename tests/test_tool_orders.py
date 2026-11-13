from datetime import UTC, datetime
from decimal import Decimal

import pytest

import app.tools.orders as orders_module
from app.tools.audit import InMemoryAuditSink, ToolStatus
from app.tools.catalog.orders import GetOrderStatus
from app.tools.orders import PgOrderRepository
from app.tools.registry import build_default_registry

pytestmark = pytest.mark.anyio

ORDER = {
    "order_number": "ORD-1001",
    "status": "shipped",
    "total": Decimal("59.90"),
    "updated_at": datetime(2026, 1, 5, 10, 0, tzinfo=UTC),
}


class FakeRepository:
    def get(self, order_number):
        return ORDER if order_number == "ORD-1001" else None


async def test_devuelve_el_estado_del_pedido():
    sink = InMemoryAuditSink()
    tool = GetOrderStatus(repository=FakeRepository(), audit=sink)

    result = await tool.run({"order_number": "ORD-1001"})

    assert result.status == ToolStatus.EXECUTED
    assert result.data == {
        "order_number": "ORD-1001",
        "status": "shipped",
        "total": "59.90",
        "updated_at": "2026-01-05T10:00:00+00:00",
    }
    assert sink.entries[0].tool_name == "get_order_status"


async def test_pedido_inexistente_es_un_error_estandar():
    result = await GetOrderStatus(repository=FakeRepository()).run({"order_number": "ORD-9"})

    assert (result.status, result.error.code) == (ToolStatus.FAILED, "not_found")


@pytest.mark.parametrize("value", ["ORD-1; DROP TABLE orders", "", "x" * 41])
async def test_rechaza_numeros_de_pedido_malformados(value):
    result = await GetOrderStatus(repository=FakeRepository()).run({"order_number": value})

    assert result.error.code == "invalid_arguments"


def test_queda_registrada_para_el_agente():
    names = [d["name"] for d in build_default_registry().declarations()]

    assert "get_order_status" in names


def test_la_consulta_es_parametrizada_y_de_solo_lectura(monkeypatch):
    captured = {}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params):
            captured["sql"], captured["params"] = sql, params
            return self

        def fetchone(self):
            return ORDER

    def fake_connect(url, **kwargs):
        captured["url"], captured["kwargs"] = url, kwargs
        return Connection()

    monkeypatch.setattr(orders_module.psycopg, "connect", fake_connect)

    assert PgOrderRepository("postgresql://ro").get("ORD-1001") == ORDER
    assert "%s" in captured["sql"]
    assert "ORD-1001" not in captured["sql"]
    assert captured["params"] == ("ORD-1001",)
    assert "default_transaction_read_only=on" in captured["kwargs"]["options"]
