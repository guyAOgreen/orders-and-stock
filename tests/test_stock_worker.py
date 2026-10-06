"""The stock worker's guarantees, proven against PostgreSQL.

Orders are accepted through the real Orders service so the work rows are the
ones the contract produces; the worker is then driven directly.
"""

import ast
import logging
import re
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

import orders_stock.stock as stock_package
from orders_stock.models import Order, Product, StockWork
from orders_stock.orders.schemas import OrderItemRequest, OrderRequest
from orders_stock.orders.service import accept_order, get_order
from orders_stock.stock.worker import process_one, run_worker

BANANAS = {"sku": "BAN-001", "name": "Bananas 1kg", "price_cents": 199, "stock": 50}
MILK = {"sku": "MLK-002", "name": "Milk 2L", "price_cents": 2599, "stock": 20}


@pytest.fixture
def products(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        session.add_all(Product(**p) for p in (BANANAS, MILK))
        session.commit()


def _accept(
    session_factory: sessionmaker[Session], order_ref: str, **quantities: int
) -> int:
    """Accept an order through the Orders service; returns its id."""
    request = OrderRequest(
        order_ref=order_ref,
        customer_id="cust-42",
        items=[OrderItemRequest(sku=sku, qty=qty) for sku, qty in quantities.items()],
    )
    with session_factory() as session:
        _, created = accept_order(session, request)
        assert created
        return session.execute(
            select(Order.id).where(Order.order_ref == order_ref)
        ).scalar_one()


def _stock(session_factory: sessionmaker[Session]) -> dict[str, int]:
    with session_factory() as session:
        rows = session.execute(select(Product.sku, Product.stock)).all()
        return {sku: stock for sku, stock in rows}


def _work_rows(session_factory: sessionmaker[Session]) -> list[dict[str, Any]]:
    with session_factory() as session:
        rows = session.execute(select(StockWork).order_by(StockWork.id)).scalars()
        return [
            {"order_id": w.order_id, "status": w.status, "processed_at": w.processed_at}
            for w in rows
        ]


def _stock_status(session_factory: sessionmaker[Session], order_ref: str) -> str:
    with session_factory() as session:
        details = get_order(session, order_ref)
        assert details is not None
        return details.stock_status


# --- One work row, one transaction -----------------------------------------


def test_process_one_decrements_stock_and_marks_the_row_processed(
    session_factory: sessionmaker[Session], products: None
) -> None:
    _accept(session_factory, "web-1", **{"BAN-001": 2, "MLK-002": 1})

    with session_factory() as session:
        processed = process_one(session)

    assert processed is True
    assert _stock(session_factory) == {"BAN-001": 48, "MLK-002": 19}
    (row,) = _work_rows(session_factory)
    assert row["status"] == "processed"
    assert row["processed_at"] is not None
    assert _stock_status(session_factory, "web-1") == "applied"


def test_process_one_with_nothing_pending_returns_false_and_ends_its_transaction(
    session_factory: sessionmaker[Session], products: None
) -> None:
    with session_factory() as session:
        processed = process_one(session)
        assert session.in_transaction() is False

    assert processed is False
    assert _stock(session_factory) == {"BAN-001": 50, "MLK-002": 20}


def test_running_again_over_processed_rows_changes_nothing(
    session_factory: sessionmaker[Session], products: None
) -> None:
    _accept(session_factory, "web-1", **{"BAN-001": 2})
    with session_factory() as session:
        assert process_one(session) is True
    before = (_stock(session_factory), _work_rows(session_factory))

    with session_factory() as session:
        assert process_one(session) is False
    with session_factory() as session:
        assert process_one(session) is False

    assert (_stock(session_factory), _work_rows(session_factory)) == before


def test_rows_are_processed_oldest_first(
    session_factory: sessionmaker[Session], products: None
) -> None:
    _accept(session_factory, "web-1", **{"BAN-001": 1})
    _accept(session_factory, "web-2", **{"BAN-001": 10})

    with session_factory() as session:
        process_one(session)

    assert _stock(session_factory)["BAN-001"] == 49
    assert [r["status"] for r in _work_rows(session_factory)] == [
        "processed",
        "pending",
    ]


# --- Failure and recovery -------------------------------------------------


class InjectedFailure(Exception):
    pass


class FailureInjector:
    """Raises at the database boundary when a matching statement is about to run.

    ``times`` bounds how many times it fires, so a transient failure can be
    followed by a successful retry.
    """

    def __init__(self, statement_prefix: str, times: int = 1) -> None:
        self.statement_prefix = statement_prefix
        self.remaining = times
        self.fired = 0

    def __call__(self, conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if self.remaining > 0 and statement.startswith(self.statement_prefix):
            self.remaining -= 1
            self.fired += 1
            raise InjectedFailure(f"injected failure before: {statement[:40]}")


@pytest.fixture
def fail_completion_marker_once(engine: Engine) -> Iterator[FailureInjector]:
    """Fail the completion marker, after the decrements have run."""
    injector = FailureInjector("UPDATE stock_work")
    event.listen(engine, "before_cursor_execute", injector)
    try:
        yield injector
    finally:
        event.remove(engine, "before_cursor_execute", injector)


def test_failure_between_decrement_and_commit_leaves_stock_and_row_unchanged(
    session_factory: sessionmaker[Session],
    products: None,
    fail_completion_marker_once: FailureInjector,
) -> None:
    _accept(session_factory, "web-1", **{"BAN-001": 2, "MLK-002": 1})

    with session_factory() as session, pytest.raises(InjectedFailure):
        process_one(session)

    assert fail_completion_marker_once.fired == 1
    assert _stock(session_factory) == {"BAN-001": 50, "MLK-002": 20}
    (row,) = _work_rows(session_factory)
    assert row["status"] == "pending"
    assert _stock_status(session_factory, "web-1") == "pending"


def test_failed_attempt_leaves_the_session_usable_for_a_retry(
    session_factory: sessionmaker[Session],
    products: None,
    fail_completion_marker_once: FailureInjector,
) -> None:
    _accept(session_factory, "web-1", **{"BAN-001": 2})

    with session_factory() as session:
        with pytest.raises(InjectedFailure):
            process_one(session)
        assert process_one(session) is True

    assert _stock(session_factory)["BAN-001"] == 48


# --- Component boundary ---------------------------------------------------


@pytest.fixture
def executed_statements(engine: Engine) -> Iterator[list[str]]:
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_worker_touches_only_the_stock_work_and_products_tables(
    session_factory: sessionmaker[Session],
    products: None,
    executed_statements: list[str],
) -> None:
    _accept(session_factory, "web-1", **{"BAN-001": 2, "MLK-002": 1})
    executed_statements.clear()

    with session_factory() as session:
        process_one(session)

    assert executed_statements, "the worker ran no SQL"
    for statement in executed_statements:
        tables = set(
            re.findall(r"\b(?:FROM|(?<!FOR )UPDATE|INTO|JOIN)\s+(\w+)", statement)
        )
        assert tables <= {"stock_work", "products"}, statement


def test_stock_module_does_not_import_orders_code() -> None:
    package_dir = Path(stock_package.__file__).parent
    for module in package_dir.glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(name.startswith("orders_stock.orders") for name in names), (
                f"{module.name} imports orders code: {names}"
            )


# --- The polling loop -----------------------------------------------------


def _run_until_idle(session_factory: sessionmaker[Session], **kwargs: Any) -> None:
    """Run the worker loop in a thread and stop it once the queue is drained."""
    stop = threading.Event()
    thread = threading.Thread(
        target=run_worker,
        kwargs={"session_factory": session_factory, "stop": stop, **kwargs},
    )
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if all(r["status"] == "processed" for r in _work_rows(session_factory)):
            break
        time.sleep(0.02)
    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive(), "worker did not stop"


def test_rows_created_while_no_worker_runs_are_processed_once_one_starts(
    session_factory: sessionmaker[Session], products: None
) -> None:
    # The interruption: orders accepted with no worker anywhere.
    _accept(session_factory, "web-1", **{"BAN-001": 2})
    _accept(session_factory, "web-2", **{"BAN-001": 3, "MLK-002": 1})
    assert [r["status"] for r in _work_rows(session_factory)] == ["pending"] * 2
    assert _stock(session_factory) == {"BAN-001": 50, "MLK-002": 20}

    # The recovery: a worker starts and catches up.
    _run_until_idle(session_factory, poll_interval=0.05)

    assert _stock(session_factory) == {"BAN-001": 45, "MLK-002": 19}
    assert [r["status"] for r in _work_rows(session_factory)] == ["processed"] * 2


def test_loop_retries_after_a_transient_failure(
    session_factory: sessionmaker[Session],
    products: None,
    fail_completion_marker_once: FailureInjector,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _accept(session_factory, "web-1", **{"BAN-001": 2})

    with caplog.at_level(logging.ERROR, logger="orders_stock"):
        _run_until_idle(session_factory, poll_interval=0.05)

    assert fail_completion_marker_once.fired == 1
    assert _stock(session_factory)["BAN-001"] == 48
    (row,) = _work_rows(session_factory)
    assert row["status"] == "processed"
    failures = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(failures) == 1
    assert "order_id=1" in failures[0].getMessage()
    assert failures[0].exc_info is not None


def test_loop_stops_promptly_when_idle(
    session_factory: sessionmaker[Session], products: None
) -> None:
    stop = threading.Event()
    thread = threading.Thread(
        target=run_worker,
        kwargs={"session_factory": session_factory, "poll_interval": 30, "stop": stop},
    )
    thread.start()
    time.sleep(0.1)

    started = time.monotonic()
    stop.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert time.monotonic() - started < 1


# --- Two workers ----------------------------------------------------------

LOCK_WAIT_TIMEOUT_SECONDS = 5.0


@dataclass
class Hold:
    """The first execution of a matching statement pauses until released."""

    statement_prefix: str
    release: threading.Event = field(default_factory=threading.Event)
    first_arrived: threading.Event = field(default_factory=threading.Event)
    held: bool = False


@pytest.fixture
def hold(engine: Engine, request: pytest.FixtureRequest) -> Iterator[Hold]:
    hold = Hold(statement_prefix=request.param)

    def pause(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if (
            not statement.startswith(hold.statement_prefix)
            or hold.first_arrived.is_set()
        ):
            return
        hold.first_arrived.set()
        hold.held = True
        assert hold.release.wait(LOCK_WAIT_TIMEOUT_SECONDS), "hold never released"

    event.listen(engine, "after_cursor_execute", pause)
    try:
        yield hold
    finally:
        hold.release.set()
        event.remove(engine, "after_cursor_execute", pause)


def _process_in_new_session(session_factory: sessionmaker[Session]) -> bool:
    with session_factory() as session:
        return process_one(session)


@pytest.mark.parametrize("hold", ["SELECT stock_work.id"], indirect=True)
def test_two_workers_never_process_the_same_row(
    session_factory: sessionmaker[Session], products: None, hold: Hold
) -> None:
    # Disjoint SKUs, so the only thing the two workers can contend for is
    # the work row itself.
    _accept(session_factory, "web-1", **{"BAN-001": 2})
    _accept(session_factory, "web-2", **{"MLK-002": 1})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_process_in_new_session, session_factory)
        assert hold.first_arrived.wait(LOCK_WAIT_TIMEOUT_SECONDS)
        # The first worker holds its claimed row locked, uncommitted.
        second = pool.submit(_process_in_new_session, session_factory)
        second_result = second.result(timeout=LOCK_WAIT_TIMEOUT_SECONDS)
        hold.release.set()
        first_result = first.result(timeout=LOCK_WAIT_TIMEOUT_SECONDS)

    assert hold.held
    assert (first_result, second_result) == (True, True)
    assert _stock(session_factory) == {"BAN-001": 48, "MLK-002": 19}
    assert [r["status"] for r in _work_rows(session_factory)] == ["processed"] * 2


def _another_backend_waits_on_a_lock(engine: Engine, query_prefix: str) -> bool:
    with engine.connect() as probe:
        waiting = probe.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "AND wait_event_type = 'Lock' AND query LIKE :pattern"
            ),
            {"pattern": f"{query_prefix}%"},
        ).scalar_one()
    return bool(waiting)


@pytest.mark.parametrize("hold", ["UPDATE products"], indirect=True)
def test_concurrent_orders_for_one_sku_produce_the_combined_deduction(
    engine: Engine, session_factory: sessionmaker[Session], products: None, hold: Hold
) -> None:
    # The first worker pauses after decrementing, before committing, until
    # the second is blocked on the product row's lock. A read-modify-write
    # would have the second overwrite the first's decrement; an atomic
    # ``stock = stock - qty`` re-reads the committed value once unblocked.
    _accept(session_factory, "web-1", **{"BAN-001": 2})
    _accept(session_factory, "web-2", **{"BAN-001": 3})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_process_in_new_session, session_factory)
        assert hold.first_arrived.wait(LOCK_WAIT_TIMEOUT_SECONDS)
        second = pool.submit(_process_in_new_session, session_factory)
        deadline = time.monotonic() + LOCK_WAIT_TIMEOUT_SECONDS
        while not _another_backend_waits_on_a_lock(engine, "UPDATE products"):
            assert time.monotonic() < deadline, "second decrement never blocked"
            time.sleep(0.02)
        hold.release.set()
        results = (first.result(timeout=5), second.result(timeout=5))

    assert results == (True, True)
    assert _stock(session_factory)["BAN-001"] == 45
