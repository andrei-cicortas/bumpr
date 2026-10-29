"""Thin service layer used by the HTTP handlers."""
import json

from .models import Item, Order


def parse_order(payload: dict) -> Order:
    return Order.parse_obj(payload)


def parse_order_json(raw: str) -> Order:
    return Order.parse_raw(raw)


def order_to_json(order: Order) -> str:
    return order.json(exclude_none=True)


def order_summary(order: Order) -> dict:
    data = order.dict(include={"id", "discount"})
    data["total"] = order.total
    return data


def restock(item: Item, amount: int) -> Item:
    return item.copy(update={"quantity": item.quantity + amount})


def item_schema() -> dict:
    return Item.schema()


def order_from_row(row) -> Order:
    return Order.from_orm(row)


def export_items(items) -> str:
    return json.dumps([i.dict() for i in items])
