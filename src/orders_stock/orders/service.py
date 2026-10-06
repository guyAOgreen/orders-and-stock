"""Accepting orders and reading their details.

Functions here own the transaction: ``accept_order`` commits or rolls back the
session it is given. See "Accepting an order" in SOLUTION.md.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
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
    ``order_ref`` returns the existing order and writes nothing; the payload of
    the repeat is not validated or compared.
    """
    existing = get_order(session, request.order_ref)
    if existing is not None:
        return existing, False

    quantities = _merge_quantities(request.items)
    prices = _current_prices(session, list(quantities))
    unknown = [sku for sku in quantities if sku not in prices]
    if unknown:
        raise UnknownSkuError(unknown)
    total_cents = sum(prices[sku] * qty for sku, qty in quantities.items())
    _check_ranges(quantities, total_cents)

    # The unique constraint on order_ref arbitrates concurrent duplicates: the
    # loser's insert returns no row and this transaction writes nothing.
    order_id = session.execute(
        insert(Order)
        .values(
            order_ref=request.order_ref,
            customer_id=request.customer_id,
            total_cents=total_cents,
        )
        .on_conflict_do_nothing(constraint="uq_orders_order_ref")
        .returning(Order.id)
    ).scalar_one_or_none()
    if order_id is None:
        session.rollback()
        winner = get_order(session, request.order_ref)
        if winner is None:  # pragma: no cover - the winning transaction committed
            raise RuntimeError(f"order {request.order_ref!r} conflicted but is absent")
        return winner, False

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
    session.commit()

    created = get_order(session, request.order_ref)
    if created is None:  # pragma: no cover - committed a moment ago
        raise RuntimeError(f"order {request.order_ref!r} missing after commit")
    return created, True


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
            for item in sorted(order.items, key=lambda item: item.id)
        ],
        total_cents=order.total_cents,
        stock_status="applied" if work_status == "processed" else "pending",
        accepted_at=order.accepted_at,
    )


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
