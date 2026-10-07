"""Request and response schemas for the Stock API.

These are the HTTP contract; the database models live in ``orders_stock.models``.
"""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

# The Stock component owns its own copy of the identifier rules rather than
# importing them from Orders, so the two API contracts can evolve apart.
IDENTIFIER_MAX_LENGTH = 128


def _no_nul(value: str) -> str:
    # PostgreSQL text cannot hold NUL; psycopg rejects it before the query
    # runs, which would surface as a 500 rather than a validation error.
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


Sku = Annotated[
    str,
    Field(min_length=1, max_length=IDENTIFIER_MAX_LENGTH),
    AfterValidator(_no_nul),
]


class StockResponse(BaseModel):
    sku: str
    name: str
    stock: int
