import json
from types import SimpleNamespace

import pytest

from app.models import Item, Order
from app import service

PAYLOAD = {
    "id": 7,
    "created_at": "2025-03-01T10:00:00",
    "discount": 0.1,
    "items": [
        {"sku": "ABC-1234", "name": "  Widget ", "price": 9.999, "quantity": 3, "tags": ["Tools", "SALE"]},
        {"sku": "XYZ-0001", "name": "Gadget", "price": 20, "quantity": 1},
    ],
}


def test_parse_and_normalise():
    order = service.parse_order(PAYLOAD)
    widget = order.items[0]
    assert widget.name == "Widget"
    assert widget.price == 10.0
    assert widget.tags == ["tools", "sale"]
    assert order.total == 45.0


def test_invalid_sku_rejected():
    with pytest.raises(ValueError):
        Item(sku="bad", name="x", price=1)


def test_blank_name_rejected():
    with pytest.raises(ValueError):
        Item(sku="ABC-1234", name="   ", price=1)


def test_items_are_immutable():
    item = Item(sku="ABC-1234", name="Widget", price=1)
    with pytest.raises(Exception):
        item.price = 2


def test_discount_requires_items():
    with pytest.raises(ValueError):
        Order(id=1, items=[], created_at="2025-01-01T00:00:00", discount=0.2)


def test_json_roundtrip():
    order = service.parse_order(PAYLOAD)
    raw = service.order_to_json(order)
    assert "note" not in json.loads(raw)
    assert service.parse_order_json(raw) == order


def test_summary():
    order = service.parse_order(PAYLOAD)
    assert service.order_summary(order) == {"id": 7, "discount": 0.1, "total": 45.0}


def test_restock_returns_new_item():
    item = Item(sku="ABC-1234", name="Widget", price=1, quantity=2)
    new = service.restock(item, 5)
    assert new.quantity == 7 and item.quantity == 2


def test_schema_has_pattern():
    schema = service.item_schema()
    assert schema["properties"]["sku"]["pattern"] == r"^[A-Z]{3}-\d{4}$"


def test_order_from_row():
    row = SimpleNamespace(id=3, items=[], created_at="2025-01-01T00:00:00", discount=None, note="x")
    assert service.order_from_row(row).note == "x"


def test_export_items():
    items = [Item(sku="ABC-1234", name="Widget", price=1)]
    assert json.loads(service.export_items(items))[0]["sku"] == "ABC-1234"
