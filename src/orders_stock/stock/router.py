"""HTTP routes for the Stock capability."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from orders_stock.api.deps import SessionDep
from orders_stock.models import Product
from orders_stock.stock.schemas import Sku, StockResponse

router = APIRouter(prefix="/stock", tags=["stock"])


# A query parameter rather than a path segment: SKUs are free text in the
# catalogue, and a SKU containing "/" or "." could not be read from a path.
@router.get("", responses={status.HTTP_404_NOT_FOUND: {}})
def read_stock(
    sku: Annotated[Sku, Query(description="The SKU to read current stock for")],
    session: SessionDep,
) -> StockResponse:
    product = session.execute(
        select(Product).where(Product.sku == sku)
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown SKU")
    return StockResponse(sku=product.sku, name=product.name, stock=product.stock)
