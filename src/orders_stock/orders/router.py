"""HTTP routes for the Orders capability."""

from fastapi import APIRouter, HTTPException, Response, status

from orders_stock.api.deps import SessionDep
from orders_stock.orders.schemas import (
    OrderItemResponse,
    OrderRequest,
    OrderResponse,
)
from orders_stock.orders.service import (
    AmountOutOfRangeError,
    OrderDetails,
    UnknownSkuError,
    accept_order,
    get_order,
)

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": OrderResponse,
            "description": "An order with this order_ref already exists; it is "
            "returned unchanged and nothing is written.",
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Invalid request body (Pydantic's list of field "
            "errors in `detail`), or a request the catalogue cannot satisfy: "
            "an unknown SKU or a quantity or total beyond the database's "
            'range, with a string `detail` such as `"Unknown SKU(s): ..."`.',
        },
    },
)
def create_order(
    payload: OrderRequest, session: SessionDep, response: Response
) -> OrderResponse:
    try:
        details, created = accept_order(session, payload)
    except (UnknownSkuError, AmountOutOfRangeError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    if not created:
        response.status_code = status.HTTP_200_OK
    return _to_response(details)


@router.get("/{order_ref}", responses={status.HTTP_404_NOT_FOUND: {}})
def read_order(order_ref: str, session: SessionDep) -> OrderResponse:
    details = get_order(session, order_ref)
    if details is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Order not found")
    return _to_response(details)


def _to_response(details: OrderDetails) -> OrderResponse:
    return OrderResponse(
        order_ref=details.order_ref,
        customer_id=details.customer_id,
        items=[
            OrderItemResponse(
                sku=line.sku, qty=line.qty, unit_price_cents=line.unit_price_cents
            )
            for line in details.items
        ],
        total_cents=details.total_cents,
        status="accepted",
        stock_status=details.stock_status,
        accepted_at=details.accepted_at,
    )
