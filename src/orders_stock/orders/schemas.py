"""Request and response schemas for the Orders API.

These are the HTTP contract; the database models live in ``orders_stock.models``.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# PostgreSQL ``integer``, which holds product prices, quantities and unit prices.
PG_INTEGER_MAX = 2**31 - 1
# PostgreSQL ``bigint``, which holds order totals.
PG_BIGINT_MAX = 2**63 - 1

StockStatus = Literal["pending", "applied"]


class OrderItemRequest(BaseModel):
    sku: str = Field(min_length=1)
    qty: int = Field(gt=0, le=PG_INTEGER_MAX)


class OrderRequest(BaseModel):
    order_ref: str = Field(min_length=1, description="Client idempotency key")
    customer_id: str = Field(min_length=1)
    items: list[OrderItemRequest] = Field(min_length=1)


class OrderItemResponse(BaseModel):
    sku: str
    qty: int
    unit_price_cents: int


class OrderResponse(BaseModel):
    order_ref: str
    customer_id: str
    items: list[OrderItemResponse]
    total_cents: int
    status: Literal["accepted"]
    stock_status: StockStatus
    accepted_at: datetime
