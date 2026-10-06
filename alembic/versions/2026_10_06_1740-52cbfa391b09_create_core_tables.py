"""Create products, orders, order_items and stock_work.

Constraint names follow the naming convention on ``orders_stock.db.Base`` so
later migrations and tests can refer to them.

Revision ID: 52cbfa391b09
Revises: f2e92933e0ec
Create Date: 2026-10-06 17:40:38.030935
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "52cbfa391b09"
down_revision: str | Sequence[str] | None = "f2e92933e0ec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        # No non-negative check: insufficient stock is out of scope (SOLUTION.md).
        sa.Column("stock", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "price_cents >= 0", name=op.f("ck_products_price_cents_non_negative")
        ),
        sa.PrimaryKeyConstraint("sku", name=op.f("pk_products")),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("order_ref", sa.Text(), nullable=False),
        sa.Column("customer_id", sa.Text(), nullable=False),
        sa.Column("total_cents", sa.BigInteger(), nullable=False),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "total_cents >= 0", name=op.f("ck_orders_total_cents_non_negative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("order_ref", name=op.f("uq_orders_order_ref")),
    )
    op.create_index(op.f("ix_orders_accepted_at"), "orders", ["accepted_at"])

    op.create_table(
        "order_items",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_order_items_quantity_positive")
        ),
        sa.CheckConstraint(
            "unit_price_cents >= 0",
            name=op.f("ck_order_items_unit_price_cents_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_order_items_order_id_orders")
        ),
        sa.ForeignKeyConstraint(
            ["sku"], ["products.sku"], name=op.f("fk_order_items_sku_products")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_items")),
    )
    op.create_index(op.f("ix_order_items_order_id"), "order_items", ["order_id"])

    op.create_table(
        "stock_work",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'processed')",
            name=op.f("ck_stock_work_status_valid"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(items) = 'array'", name=op.f("ck_stock_work_items_is_array")
        ),
        sa.CheckConstraint(
            "(status = 'processed') = (processed_at IS NOT NULL)",
            name=op.f("ck_stock_work_processed_at_matches_status"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_stock_work_order_id_orders")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_work")),
        sa.UniqueConstraint("order_id", name=op.f("uq_stock_work_order_id")),
    )
    # The worker claims the oldest pending row; processed rows only accumulate.
    op.create_index(
        op.f("ix_stock_work_pending_created_at"),
        "stock_work",
        ["created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_table("stock_work")
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("products")
