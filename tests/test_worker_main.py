"""The worker process: startup, the loop and graceful shutdown on a signal.

Signal handlers must be installed from the main thread, so ``run`` is called
directly in the test and the signal is raised from a hook or a timer.
"""

import logging
import signal
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from orders_stock.config import Settings
from orders_stock.models import Product, StockWork
from orders_stock.orders.schemas import OrderItemRequest, OrderRequest
from orders_stock.orders.service import accept_order
from orders_stock.worker_main import run


@pytest.fixture
def fast_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"worker_poll_interval_seconds": 0.05})


@pytest.fixture
def pending_orders(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        session.add(
            Product(sku="BAN-001", name="Bananas 1kg", price_cents=199, stock=50)
        )
        session.commit()
        for ref, qty in (("web-1", 2), ("web-2", 3)):
            request = OrderRequest(
                order_ref=ref,
                customer_id="cust-42",
                items=[OrderItemRequest(sku="BAN-001", qty=qty)],
            )
            accept_order(session, request)


def _state(session_factory: sessionmaker[Session]) -> tuple[int, list[str]]:
    with session_factory() as session:
        stock = session.execute(
            select(Product.stock).where(Product.sku == "BAN-001")
        ).scalar_one()
        statuses = (
            session.execute(select(StockWork.status).order_by(StockWork.id))
            .scalars()
            .all()
        )
    return stock, list(statuses)


@pytest.fixture
def sigint_after(settings: Settings) -> Iterator[threading.Timer]:
    timer = threading.Timer(0.3, signal.raise_signal, args=(signal.SIGINT,))
    try:
        yield timer
    finally:
        timer.cancel()


def test_worker_runs_until_interrupted_and_exits_cleanly(
    fast_settings: Settings,
    pending_orders: None,
    session_factory: sessionmaker[Session],
    sigint_after: threading.Timer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sigint_after.start()
    with caplog.at_level(logging.INFO, logger="orders_stock"):
        exit_code = run(fast_settings)

    assert exit_code == 0
    assert _state(session_factory) == (45, ["processed", "processed"])
    messages = [record.getMessage() for record in caplog.records]
    assert any("started" in m for m in messages)
    assert any("stopping" in m for m in messages)
    assert any("stopped" in m for m in messages)


@pytest.fixture
def sigint_during_first_decrement() -> Iterator[list[str]]:
    """Raise SIGINT from inside the first stock decrement, mid-transaction.

    Listens at the ``Engine`` class level because ``run`` builds its own engine.
    """
    fired: list[str] = []

    def interrupt(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if statement.startswith("UPDATE products") and not fired:
            fired.append(statement)
            signal.raise_signal(signal.SIGINT)

    event.listen(Engine, "after_cursor_execute", interrupt)
    try:
        yield fired
    finally:
        event.remove(Engine, "after_cursor_execute", interrupt)


def test_signal_during_a_transaction_finishes_it_and_starts_no_other(
    fast_settings: Settings,
    pending_orders: None,
    session_factory: sessionmaker[Session],
    sigint_during_first_decrement: list[str],
) -> None:
    # Backstop so a missed signal fails the assertions instead of hanging.
    backstop = threading.Timer(5, signal.raise_signal, args=(signal.SIGINT,))
    backstop.start()
    try:
        exit_code = run(fast_settings)
    finally:
        backstop.cancel()

    assert exit_code == 0
    assert len(sigint_during_first_decrement) == 1
    # The in-flight order committed whole; the next one was never started.
    assert _state(session_factory) == (48, ["processed", "pending"])


def test_worker_fails_when_database_is_unreachable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    unreachable = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@127.0.0.1:1/nope?connect_timeout=1",
    )

    with caplog.at_level(logging.ERROR, logger="orders_stock"):
        exit_code = run(unreachable)

    assert exit_code == 1
    assert any("database" in r.getMessage() for r in caplog.records)


def test_signal_handlers_are_restored_after_run(
    fast_settings: Settings, sigint_after: threading.Timer
) -> None:
    before = signal.getsignal(signal.SIGINT)
    sigint_after.start()

    run(fast_settings)

    assert signal.getsignal(signal.SIGINT) is before
