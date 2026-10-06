"""The schema's constraints, proven against PostgreSQL.

These tests insert through the SQLAlchemy models so a model that drifts from
the migrated tables also fails here.
"""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from orders_stock.models import Order, OrderItem, Product, StockWork

DOMAIN_TABLES = {"products", "orders", "order_items", "stock_work"}


def _table_names(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _product(sku: str = "BAN-001", **overrides: object) -> Product:
    values: dict[str, object] = {
        "sku": sku,
        "name": "Bananas 1kg",
        "price_cents": 199,
        "stock": 50,
    }
    values.update(overrides)
    return Product(**values)


def _order(order_ref: str = "web-100045", **overrides: object) -> Order:
    values: dict[str, object] = {
        "order_ref": order_ref,
        "customer_id": "cust-42",
        "total_cents": 398,
    }
    values.update(overrides)
    return Order(**values)


def _commit(session_factory: sessionmaker[Session], *rows: object) -> None:
    with session_factory() as session:
        session.add_all(rows)
        session.commit()


def _commit_rejected(
    session_factory: sessionmaker[Session], constraint: str, *rows: object
) -> None:
    with session_factory() as session:
        session.add_all(rows)
        with pytest.raises(IntegrityError, match=constraint):
            session.commit()


def _committed_order(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
        order = _order()
        session.add(order)
        session.commit()
        return order.id


def test_migrations_round_trip_from_an_empty_database(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "base")
    assert _table_names(engine).isdisjoint(DOMAIN_TABLES)

    command.upgrade(alembic_config, "head")
    assert DOMAIN_TABLES <= _table_names(engine)


def test_second_order_with_same_order_ref_is_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    _commit(session_factory, _order("web-1"))

    _commit_rejected(session_factory, "uq_orders_order_ref", _order("web-1"))


def test_second_work_row_for_same_order_is_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    order_id = _committed_order(session_factory)
    _commit(session_factory, StockWork(order_id=order_id, items=[]))

    _commit_rejected(
        session_factory,
        "uq_stock_work_order_id",
        StockWork(order_id=order_id, items=[]),
    )


def test_work_row_for_unknown_order_is_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    _commit_rejected(
        session_factory,
        "fk_stock_work_order_id_orders",
        StockWork(order_id=999_999, items=[]),
    )


def test_order_item_for_unknown_sku_is_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    order_id = _committed_order(session_factory)

    _commit_rejected(
        session_factory,
        "fk_order_items_sku_products",
        OrderItem(order_id=order_id, sku="NOPE-000", quantity=1, unit_price_cents=1),
    )


def test_new_work_row_is_pending_with_timezone_aware_timestamps(
    session_factory: sessionmaker[Session],
) -> None:
    order_id = _committed_order(session_factory)

    with session_factory() as session:
        work = StockWork(order_id=order_id, items=[{"sku": "BAN-001", "qty": 2}])
        session.add(work)
        session.commit()
        session.refresh(work)

        assert work.status == "pending"
        assert work.processed_at is None
        assert work.created_at.tzinfo is not None
        assert work.items == [{"sku": "BAN-001", "qty": 2}]


def test_order_acceptance_timestamp_is_timezone_aware(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        order = _order()
        session.add(order)
        session.commit()
        session.refresh(order)

        assert order.accepted_at.tzinfo is not None


@pytest.mark.parametrize(
    ("status", "processed_at"),
    [
        ("processed", None),
        ("pending", datetime(2026, 10, 6, 12, 0, tzinfo=UTC)),
    ],
    ids=["processed-without-timestamp", "pending-with-timestamp"],
)
def test_processed_at_must_match_status(
    session_factory: sessionmaker[Session],
    status: str,
    processed_at: datetime | None,
) -> None:
    order_id = _committed_order(session_factory)

    _commit_rejected(
        session_factory,
        "ck_stock_work_processed_at_matches_status",
        StockWork(
            order_id=order_id, status=status, processed_at=processed_at, items=[]
        ),
    )


def test_unknown_work_status_is_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    order_id = _committed_order(session_factory)

    _commit_rejected(
        session_factory,
        "ck_stock_work_status_valid",
        StockWork(order_id=order_id, status="done", items=[]),
    )


def test_work_items_must_be_a_json_array(
    session_factory: sessionmaker[Session],
) -> None:
    order_id = _committed_order(session_factory)

    _commit_rejected(
        session_factory,
        "ck_stock_work_items_is_array",
        StockWork(order_id=order_id, items={"sku": "BAN-001", "qty": 2}),
    )


def test_stock_may_go_negative(session_factory: sessionmaker[Session]) -> None:
    _commit(session_factory, _product(stock=1))

    with session_factory() as session:
        session.execute(
            text("UPDATE products SET stock = stock - :qty WHERE sku = :sku"),
            {"qty": 2, "sku": "BAN-001"},
        )
        session.commit()

        assert session.get_one(Product, "BAN-001").stock == -1


@pytest.mark.parametrize(
    ("constraint", "build"),
    [
        (
            "ck_products_price_cents_non_negative",
            lambda order_id: _product(price_cents=-1),
        ),
        (
            "ck_orders_total_cents_non_negative",
            lambda order_id: _order("web-2", total_cents=-1),
        ),
        (
            "ck_order_items_quantity_positive",
            lambda order_id: OrderItem(
                order_id=order_id, sku="BAN-001", quantity=0, unit_price_cents=199
            ),
        ),
        (
            "ck_order_items_unit_price_cents_non_negative",
            lambda order_id: OrderItem(
                order_id=order_id, sku="BAN-001", quantity=1, unit_price_cents=-1
            ),
        ),
    ],
    ids=["negative-price", "negative-total", "zero-quantity", "negative-unit-price"],
)
def test_invalid_quantities_and_amounts_are_rejected(
    session_factory: sessionmaker[Session],
    constraint: str,
    build: Callable[[int], object],
) -> None:
    _commit(session_factory, _product())
    order_id = _committed_order(session_factory)

    _commit_rejected(session_factory, constraint, build(order_id))
