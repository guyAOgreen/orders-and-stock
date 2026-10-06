"""Accepting orders and reading their details.

``accept_order`` owns the write transaction: it commits a successful
acceptance and rolls back a conflicting or failed one, so a caller may keep
using the session afterwards. See "Accepting an order" in SOLUTION.md.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from orders_stock.models import Order, OrderItem, Product, StockWork
from orders_stock.orders.schemas import (
    PG_BIGINT_MAX,
    PG_INTEGER_MAX,
    OrderItemRequest,
    OrderRequest,
    StockStatus,
)


class UnknownSkuError(ValueError):
    """The request names SKUs that are not in the products table."""

    def __init__(self, skus: list[str]) -> None:
        super().__init__(f"Unknown SKU(s): {', '.join(skus)}")
        self.skus = skus


class AmountOutOfRangeError(ValueError):
    """A merged quantity or the order total does not fit its database column."""


@dataclass(frozen=True)
class OrderLine:
    sku: str
    qty: int
    unit_price_cents: int


@dataclass(frozen=True)
class OrderDetails:
    order_ref: str
    customer_id: str
    items: list[OrderLine]
    total_cents: int
    stock_status: StockStatus
    accepted_at: datetime


def accept_order(session: Session, request: OrderRequest) -> tuple[OrderDetails, bool]:
    """Accept an order idempotently.

    Returns the order's details and whether this call created it. A repeated
    ``order_ref`` returns the existing order and writes nothing; the repeat's
    payload is neither validated against the catalogue nor compared, even
    while the original is still being accepted on another connection.
    """
    try:
        claimed = _claim_order_ref(session, request)
        if claimed is None:
            # The order_ref exists, committed or in flight on another
            # connection: this transaction wrote nothing.
            session.rollback()
        else:
            order_id, accepted_at = claimed
            quantities = _merge_quantities(request.items)
            prices = _current_prices(session, list(quantities))
            unknown = [sku for sku in quantities if sku not in prices]
            if unknown:
                raise UnknownSkuError(unknown)
            total_cents = sum(prices[sku] * qty for sku, qty in quantities.items())
            _check_ranges(quantities, total_cents)
            _complete_order(session, order_id, total_cents, quantities, prices)
            session.commit()
            return (
                OrderDetails(
                    order_ref=request.order_ref,
                    customer_id=request.customer_id,
                    items=[
                        OrderLine(sku=sku, qty=qty, unit_price_cents=prices[sku])
                        for sku, qty in quantities.items()
                    ],
                    total_cents=total_cents,
                    stock_status="pending",
                    accepted_at=accepted_at,
                ),
                True,
            )
    except BaseException:
        session.rollback()
        raise

    existing = get_order(session, request.order_ref)
    if existing is None:  # pragma: no cover - the winner's transaction committed
        raise RuntimeError(f"order {request.order_ref!r} conflicted but is absent")
    return existing, False


def get_order(session: Session, order_ref: str) -> OrderDetails | None:
    """Read an order with its items and the stock status from its work row."""
    order = session.execute(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.order_ref == order_ref)
    ).scalar_one_or_none()
    if order is None:
        return None
    work_status = session.execute(
        select(StockWork.status).where(StockWork.order_id == order.id)
    ).scalar_one_or_none()
    return OrderDetails(
        order_ref=order.order_ref,
        customer_id=order.customer_id,
        items=[
            OrderLine(
                sku=item.sku, qty=item.quantity, unit_price_cents=item.unit_price_cents
            )
            for item in order.items
        ],
        total_cents=order.total_cents,
        stock_status="applied" if work_status == "processed" else "pending",
        accepted_at=order.accepted_at,
    )


def _claim_order_ref(
    session: Session, request: OrderRequest
) -> tuple[int, datetime] | None:
    """Insert the order row with a provisional total, or return ``None``.

    This runs before any validation so that the unique constraint on
    order_ref is the sole arbiter of duplicates, including a repeat that
    arrives while the original is still uncommitted: the repeat's insert waits
    for the original's transaction and then returns no row. The provisional
    total is replaced before commit; a validation failure rolls the row back.
    """
    row = session.execute(
        insert(Order)
        .values(
            order_ref=request.order_ref,
            customer_id=request.customer_id,
            total_cents=0,
        )
        .on_conflict_do_nothing(constraint="uq_orders_order_ref")
        .returning(Order.id, Order.accepted_at)
    ).one_or_none()
    return None if row is None else (row.id, row.accepted_at)


def _merge_quantities(items: list[OrderItemRequest]) -> dict[str, int]:
    """One entry per SKU, in first-seen order, summing repeated SKUs."""
    quantities: dict[str, int] = {}
    for item in items:
        quantities[item.sku] = quantities.get(item.sku, 0) + item.qty
    return quantities


def _current_prices(session: Session, skus: list[str]) -> dict[str, int]:
    rows = session.execute(
        select(Product.sku, Product.price_cents).where(Product.sku.in_(skus))
    ).all()
    return {sku: price for sku, price in rows}


def _check_ranges(quantities: dict[str, int], total_cents: int) -> None:
    too_large = [sku for sku, qty in quantities.items() if qty > PG_INTEGER_MAX]
    if too_large:
        raise AmountOutOfRangeError(
            f"Quantity too large for SKU(s): {', '.join(too_large)}"
        )
    if total_cents > PG_BIGINT_MAX:
        raise AmountOutOfRangeError("Order total too large")


def _complete_order(
    session: Session,
    order_id: int,
    total_cents: int,
    quantities: dict[str, int],
    prices: dict[str, int],
) -> None:
    """Store the total, the priced items and the pending stock work."""
    session.execute(
        update(Order).where(Order.id == order_id).values(total_cents=total_cents)
    )
    session.execute(
        insert(OrderItem),
        [
            {
                "order_id": order_id,
                "sku": sku,
                "quantity": qty,
                "unit_price_cents": prices[sku],
            }
            for sku, qty in quantities.items()
        ],
    )
    session.execute(
        insert(StockWork).values(
            order_id=order_id,
            items=[{"sku": sku, "qty": qty} for sku, qty in quantities.items()],
        )
    )
