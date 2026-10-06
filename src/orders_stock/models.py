"""SQLAlchemy models for every table.

The schema is one shared layer in one database, so one module holds all four
mapped classes. The component boundary is which tables each component reads
and writes (see SOLUTION.md), not where the classes are defined:

- Orders reads ``products`` for prices and writes ``orders``, ``order_items``
  and a pending ``stock_work`` row, all in one transaction.
- The stock worker reads ``stock_work`` and writes ``products``; it never
  touches the Orders tables.

API request and response schemas are Pydantic models elsewhere, not these.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from orders_stock.db import Base


class Product(Base):
    """A product and its current stock level, keyed by SKU."""

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price_cents >= 0", name="price_cents_non_negative"),
    )

    sku: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    price_cents: Mapped[int] = mapped_column(Integer)
    # Deliberately unconstrained: insufficient stock is out of scope and the
    # level may go negative. See "Insufficient stock" in SOLUTION.md.
    stock: Mapped[int] = mapped_column(Integer)


class Order(Base):
    """An accepted order. ``order_ref`` is the client's idempotency key."""

    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("total_cents >= 0", name="total_cents_non_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    order_ref: Mapped[str] = mapped_column(Text, unique=True)
    customer_id: Mapped[str] = mapped_column(Text)
    total_cents: Mapped[int] = mapped_column(BigInteger)
    # Indexed for the daily report, which scans one UTC day of acceptances.
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    """One line of an order, with the unit price in effect at acceptance."""

    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price_cents >= 0", name="unit_price_cents_non_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_cents: Mapped[int] = mapped_column(Integer)

    order: Mapped[Order] = relationship(back_populates="items")


class StockWork(Base):
    """Pending stock work for one order: the contract between Orders and Stock.

    ``items`` is a JSON array of ``{"sku": str, "qty": int}`` objects. The
    schema guarantees only that it is an array; the Orders component builds a
    valid payload at acceptance and the stock worker consumes it.
    """

    __tablename__ = "stock_work"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'processed')", name="status_valid"),
        CheckConstraint("jsonb_typeof(items) = 'array'", name="items_is_array"),
        CheckConstraint(
            "(status = 'processed') = (processed_at IS NOT NULL)",
            name="processed_at_matches_status",
        ),
        # The worker claims the oldest pending row; processed rows only grow.
        Index(
            "ix_stock_work_pending_created_at",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True)
    status: Mapped[str] = mapped_column(
        Text, default="pending", server_default="pending"
    )
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
