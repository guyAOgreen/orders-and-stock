"""Request and response schemas for the Orders API.

These are the HTTP contract; the database models live in ``orders_stock.models``.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

# PostgreSQL ``integer``, which holds product prices, quantities and unit prices.
PG_INTEGER_MAX = 2**31 - 1
# PostgreSQL ``bigint``, which holds order totals.
PG_BIGINT_MAX = 2**63 - 1

StockStatus = Literal["pending", "applied"]

# URL "unreserved" characters (RFC 3986), so an order_ref needs no encoding and
# round-trips through the GET path, where a slash would split the segment.
ORDER_REF_PATTERN = r"^[A-Za-z0-9._~-]+$"
ORDER_REF_MAX_LENGTH = 128


def _not_a_dot_segment(order_ref: str) -> str:
    # "." and ".." pass the pattern but are special path segments that URL
    # normalisation removes, so they could never reach the GET route.
    if set(order_ref) == {"."}:
        raise ValueError("order_ref must contain a character other than '.'")
    return order_ref


class OrderItemRequest(BaseModel):
    sku: str = Field(min_length=1)
    qty: int = Field(gt=0, le=PG_INTEGER_MAX)


class OrderRequest(BaseModel):
    order_ref: Annotated[str, AfterValidator(_not_a_dot_segment)] = Field(
        min_length=1,
        max_length=ORDER_REF_MAX_LENGTH,
        pattern=ORDER_REF_PATTERN,
        description="Client idempotency key: letters, digits, `.`, `_`, `~`, `-`",
    )
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
