"""Inventory and order models shared by the warehouse API."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, root_validator, validator


class Item(BaseModel):
    sku: str = Field(..., regex=r"^[A-Z]{3}-\d{4}$")
    name: str
    price: float
    quantity: int = 0
    tags: List[str] = []

    class Config:
        anystr_strip_whitespace = True
        allow_mutation = False

    @validator("name")
    def name_not_blank(cls, v):
        if not v:
            raise ValueError("name must not be blank")
        return v

    @validator("price")
    def price_positive(cls, v):
        if v <= 0:
            raise ValueError("price must be positive")
        return round(v, 2)

    @validator("tags", each_item=True)
    def tags_lowercase(cls, v):
        return v.lower()


class Order(BaseModel):
    id: int
    items: List[Item]
    created_at: datetime
    discount: Optional[float] = None
    note: Optional[str] = None

    class Config:
        orm_mode = True

    @root_validator
    def discount_needs_items(cls, values):
        if values.get("discount") and not values.get("items"):
            raise ValueError("discount requires at least one item")
        return values

    @property
    def total(self) -> float:
        gross = sum(i.price * i.quantity for i in self.items)
        return round(gross * (1 - (self.discount or 0)), 2)
