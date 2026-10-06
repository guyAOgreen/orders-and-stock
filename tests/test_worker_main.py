"""The worker process: startup, the loop and graceful shutdown on a signal.

Signal handlers must be installed from the main thread, so ``run`` is called
directly in the test. The signal is raised from inside a cursor hook on a
statement the worker runs, which only happens after ``run`` has installed its
handlers; a fixed timer could fire during a slow startup and abort pytest
with a plain KeyboardInterrupt instead.
"""

import logging
import signal
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from orders_stock.config import Settings
from orders_stock.models import Product, StockWork
from orders_stock.orders.schemas import OrderItemRequest, OrderRequest
from orders_stock.orders.service import accept_order
from orders_stock.worker_main import run

# Prefixes of the statements the worker runs, as SQLAlchemy compiles them.
CLAIM = "SELECT stock_work.id"
DECREMENT = "UPDATE products"


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


@dataclass(eq=False)  # hashable, as SQLAlchemy's event registry requires
class SigintOnStatement:
    """Raise SIGINT right after the ``nth`` execution of a matching statement."""

    prefix: str
    nth: int
    seen: int = 0
    fired: list[str] = field(default_factory=list)

    def __call__(self, conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if not statement.startswith(self.prefix) or self.fired:
            return
        self.seen += 1
        if self.seen == self.nth:
            self.fired.append(statement)
            signal.raise_signal(signal.SIGINT)


@pytest.fixture
def sigint_on(request: pytest.FixtureRequest) -> Iterator[SigintOnStatement]:
    """Parametrised indirectly with ``(statement prefix, nth execution)``.

    Listens at the ``Engine`` class level because ``run`` builds its own engine.
    """
    prefix, nth = request.param
    trigger = SigintOnStatement(prefix, nth)
    event.listen(Engine, "after_cursor_execute", trigger)
    try:
        yield trigger
    finally:
        event.remove(Engine, "after_cursor_execute", trigger)


def _run_with_backstop(settings: Settings) -> int:
    """Run the worker; if the expected signal never comes, send one after a
    generous delay so the test fails on its assertions instead of hanging."""
    backstop = threading.Timer(10, signal.raise_signal, args=(signal.SIGINT,))
    backstop.start()
    try:
        return run(settings)
    finally:
        backstop.cancel()


# After two rows are processed the third claim finds nothing: an idle worker.
@pytest.mark.parametrize("sigint_on", [(CLAIM, 3)], indirect=True)
def test_worker_drains_the_backlog_and_exits_cleanly_when_interrupted(
    fast_settings: Settings,
    pending_orders: None,
    session_factory: sessionmaker[Session],
    sigint_on: SigintOnStatement,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="orders_stock"):
        exit_code = _run_with_backstop(fast_settings)

    assert exit_code == 0
    assert sigint_on.fired
    assert _state(session_factory) == (45, ["processed", "processed"])
    messages = [record.getMessage() for record in caplog.records]
    assert any("started" in m for m in messages)
    assert any("stopping" in m for m in messages)
    assert any("stopped" in m for m in messages)


@pytest.mark.parametrize("sigint_on", [(DECREMENT, 1)], indirect=True)
def test_signal_during_a_transaction_finishes_it_and_starts_no_other(
    fast_settings: Settings,
    pending_orders: None,
    session_factory: sessionmaker[Session],
    sigint_on: SigintOnStatement,
) -> None:
    exit_code = _run_with_backstop(fast_settings)

    assert exit_code == 0
    assert sigint_on.fired
    # The in-flight order committed whole; the next one was never started.
    assert _state(session_factory) == (48, ["processed", "pending"])


@pytest.mark.parametrize("sigint_on", [(CLAIM, 1)], indirect=True)
def test_signal_after_a_claim_releases_the_row_unapplied(
    fast_settings: Settings,
    pending_orders: None,
    session_factory: sessionmaker[Session],
    sigint_on: SigintOnStatement,
) -> None:
    # The signal lands between the loop's stop check and the first decrement.
    # The claimed row must be released, not applied, so that a shutdown
    # request never starts another order.
    exit_code = _run_with_backstop(fast_settings)

    assert exit_code == 0
    assert sigint_on.fired
    assert _state(session_factory) == (50, ["pending", "pending"])


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


@pytest.mark.parametrize("sigint_on", [(CLAIM, 1)], indirect=True)
def test_signal_handlers_are_restored_after_run(
    fast_settings: Settings, sigint_on: SigintOnStatement
) -> None:
    before = signal.getsignal(signal.SIGINT)

    _run_with_backstop(fast_settings)

    assert signal.getsignal(signal.SIGINT) is before
