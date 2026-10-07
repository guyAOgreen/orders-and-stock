"""The daily report's queries, run against one database snapshot."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orders_stock.models import Order, OrderItem, Product


@dataclass(frozen=True)
class SkuUnits:
    sku: str
    units: int


@dataclass(frozen=True)
class SkuStock:
    sku: str
    name: str
    stock: int


@dataclass(frozen=True)
class DailyReport:
    day: date
    total_orders: int
    revenue_cents: int
    units_sold: list[SkuUnits]
    current_stock: list[SkuStock]
    generated_at: datetime


def daily_report(session: Session, day: date) -> DailyReport:
    """Report on the UTC calendar day ``day``, by acceptance time.

    The queries run in one read-only ``REPEATABLE READ`` transaction, so they
    all see the same committed data: an order that commits while the report
    runs contributes to the count, revenue and units consistently or not at
    all. Stock is read from the same snapshot, but reflects only the orders
    the worker has applied. The transaction is ended before returning.
    """
    start = datetime.combine(day, time.min, tzinfo=UTC)
    end = start + timedelta(days=1)
    in_day = (Order.accepted_at >= start, Order.accepted_at < end)

    # Scoped to this transaction; the connection reverts when it is released.
    session.connection(
        execution_options={
            "isolation_level": "REPEATABLE READ",
            "postgresql_readonly": True,
        }
    )
    try:
        generated_at = session.execute(select(func.now())).scalar_one()
        total_orders, revenue_cents = session.execute(
            select(
                func.count(Order.id), func.coalesce(func.sum(Order.total_cents), 0)
            ).where(*in_day)
        ).one()
        units = session.execute(
            select(OrderItem.sku, func.sum(OrderItem.quantity))
            .join(Order, OrderItem.order_id == Order.id)
            .where(*in_day)
            .group_by(OrderItem.sku)
            .order_by(OrderItem.sku)
        ).all()
        stock = session.execute(
            select(Product.sku, Product.name, Product.stock).order_by(Product.sku)
        ).all()
    finally:
        session.rollback()

    return DailyReport(
        day=day,
        total_orders=total_orders,
        revenue_cents=revenue_cents,
        units_sold=[SkuUnits(sku=sku, units=units) for sku, units in units],
        current_stock=[
            SkuStock(sku=sku, name=name, stock=level) for sku, name, level in stock
        ],
        generated_at=generated_at,
    )
