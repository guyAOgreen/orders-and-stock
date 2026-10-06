"""Entry point for the stock worker process.

The worker is a placeholder until the stock processing loop exists: it starts,
confirms it can reach the database, and exits.
"""

import logging
import sys

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from orders_stock.config import Settings, get_settings
from orders_stock.db import make_engine
from orders_stock.logging_config import configure_logging

log = logging.getLogger("orders_stock.worker")


def run(settings: Settings) -> int:
    log.info("stock worker started")
    engine = make_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as exc:
        log.error("database connection failed: %s", exc)
        return 1
    finally:
        engine.dispose()
    log.info("database connection ok")
    log.info("stock worker stopped")
    return 0


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    sys.exit(run(settings))


if __name__ == "__main__":
    main()
