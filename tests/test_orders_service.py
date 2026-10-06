"""Transactional guarantees of order acceptance, proven against PostgreSQL.

These tests drive the service directly with their own sessions so they can
overlap transactions and inject failures at the database boundary.
"""

import ast
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

import orders_stock.orders as orders_package
from orders_stock.models import Order, OrderItem, Product, StockWork
from orders_stock.orders.schemas import OrderItemRequest, OrderRequest
from orders_stock.orders.service import accept_order

LOCK_WAIT_TIMEOUT_SECONDS = 5.0


@pytest.fixture
def product(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        session.add(
            Product(sku="BAN-001", name="Bananas 1kg", price_cents=199, stock=50)
        )
        session.commit()


def _request(order_ref: str = "web-1") -> OrderRequest:
    return OrderRequest(
        order_ref=order_ref,
        customer_id="cust-42",
        items=[OrderItemRequest(sku="BAN-001", qty=2)],
    )


def _row_counts(session_factory: sessionmaker[Session]) -> tuple[int, int, int]:
    """(orders, order_items, stock_work) row counts as another connection sees them."""

    def count(table: type[Order | OrderItem | StockWork]) -> int:
        return session.execute(select(func.count()).select_from(table)).scalar_one()

    with session_factory() as session:
        return count(Order), count(OrderItem), count(StockWork)


@pytest.fixture
def hold_first_order_insert_until_second_blocks(engine: Engine) -> Iterator[list[str]]:
    """Make two racing acceptances genuinely overlap.

    The first transaction to insert its order row pauses, still uncommitted,
    until PostgreSQL reports another backend waiting on a lock: the second
    insert blocked on the unique order_ref. Only then does the first commit.
    """
    first_arrived = threading.Event()
    observed: list[str] = []

    def _another_backend_is_waiting_on_a_lock() -> bool:
        with engine.connect() as probe:
            waiting = probe.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND wait_event_type = 'Lock' "
                    "AND query LIKE 'INSERT INTO orders%'"
                )
            ).scalar_one()
        return bool(waiting)

    def hold(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if "INSERT INTO orders" not in statement or first_arrived.is_set():
            return
        # Check-then-set is safe: the second insert is blocked inside
        # PostgreSQL until the first commits, so its hook cannot run yet.
        first_arrived.set()
        deadline = time.monotonic() + LOCK_WAIT_TIMEOUT_SECONDS
        while not _another_backend_is_waiting_on_a_lock():
            if time.monotonic() > deadline:
                raise AssertionError("second insert never blocked on the first")
            time.sleep(0.02)
        observed.append("second insert blocked while the first was uncommitted")

    event.listen(engine, "after_cursor_execute", hold)
    try:
        yield observed
    finally:
        event.remove(engine, "after_cursor_execute", hold)


def test_concurrent_duplicates_yield_one_order_and_one_work_row(
    session_factory: sessionmaker[Session],
    product: None,
    hold_first_order_insert_until_second_blocks: list[str],
) -> None:
    def submit(_: int) -> tuple[Any, bool]:
        with session_factory() as session:
            return accept_order(session, _request())

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))

    assert hold_first_order_insert_until_second_blocks, "transactions did not overlap"
    assert sorted(created for _, created in results) == [False, True]
    assert results[0][0] == results[1][0]
    assert _row_counts(session_factory) == (1, 1, 1)


class InjectedFailure(Exception):
    pass


class FailureInjector:
    """Raises at the database boundary when the stock-work row is inserted."""

    def __init__(self) -> None:
        self.armed = True
        self.fired = 0

    def __call__(self, conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if self.armed and "INSERT INTO stock_work" in statement:
            self.fired += 1
            raise InjectedFailure("stock_work insert failed")


@pytest.fixture
def fail_work_row_insert(engine: Engine) -> Iterator[FailureInjector]:
    injector = FailureInjector()
    event.listen(engine, "before_cursor_execute", injector)
    try:
        yield injector
    finally:
        event.remove(engine, "before_cursor_execute", injector)


def test_failure_before_commit_leaves_nothing_committed(
    session_factory: sessionmaker[Session],
    product: None,
    fail_work_row_insert: FailureInjector,
) -> None:
    with session_factory() as session, pytest.raises(InjectedFailure):
        accept_order(session, _request())

    assert fail_work_row_insert.fired == 1
    assert _row_counts(session_factory) == (0, 0, 0)


def test_order_can_be_accepted_after_an_earlier_attempt_failed(
    session_factory: sessionmaker[Session],
    product: None,
    fail_work_row_insert: FailureInjector,
) -> None:
    with session_factory() as session, pytest.raises(InjectedFailure):
        accept_order(session, _request())
    fail_work_row_insert.armed = False

    with session_factory() as session:
        _, created = accept_order(session, _request())

    assert created is True
    assert _row_counts(session_factory) == (1, 1, 1)


def test_orders_module_does_not_import_stock_code() -> None:
    package_dir = Path(orders_package.__file__).parent
    for module in package_dir.glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(name.startswith("orders_stock.stock") for name in names), (
                f"{module.name} imports stock code: {names}"
            )
