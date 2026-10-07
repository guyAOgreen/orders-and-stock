"""Entry point for the stock worker process.

Starts, confirms it can reach the database, then polls for pending stock
work until SIGINT or SIGTERM. Shutdown is graceful: the signal sets a flag
that the loop checks between transactions, so an order being applied is
committed or rolled back whole and no further order is started. The flag
does not interrupt a statement already waiting inside PostgreSQL, so
shutdown waits for the current database operation to finish.
"""

import logging
import signal
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from orders_stock.config import Settings, get_settings
from orders_stock.db import make_engine, make_session_factory
from orders_stock.logging_config import configure_logging
from orders_stock.stock.worker import run_worker

log = logging.getLogger("orders_stock.worker")

STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)


def run(settings: Settings) -> int:
    log.info("stock worker started")
    engine = make_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as exc:
        log.error("database connection failed: %s", exc)
        engine.dispose()
        return 1
    log.info("database connection ok")

    try:
        with _stop_on_signals() as stop:
            run_worker(
                session_factory=make_session_factory(engine),
                poll_interval=settings.worker_poll_interval_seconds,
                stop=stop,
            )
    finally:
        engine.dispose()
    log.info("stock worker stopped")
    return 0


@contextmanager
def _stop_on_signals() -> Iterator[threading.Event]:
    """Set the yielded event on SIGINT/SIGTERM; restore the handlers after."""
    stop = threading.Event()

    def request_stop(signum: int, frame: FrameType | None) -> None:
        log.info(
            "received %s, stopping after the current order", signal.strsignal(signum)
        )
        stop.set()

    previous = {sig: signal.signal(sig, request_stop) for sig in STOP_SIGNALS}
    try:
        yield stop
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    sys.exit(run(settings))


if __name__ == "__main__":
    main()
