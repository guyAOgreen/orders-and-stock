import logging

import pytest

from orders_stock.config import Settings
from orders_stock.worker_main import run


def test_worker_starts_checks_database_and_exits_cleanly(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="orders_stock"):
        exit_code = run(settings)

    assert exit_code == 0
    messages = [record.getMessage() for record in caplog.records]
    assert any("started" in m for m in messages)
    assert any("database connection ok" in m for m in messages)
    assert any("stopped" in m for m in messages)


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
