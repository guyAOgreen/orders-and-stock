"""Shared fixtures.

Integration tests run against a dedicated PostgreSQL test database, migrated
to head once per session and truncated before each test. Sessions commit for
real so concurrency behaviour can be tested from several connections. The
database must be reachable: tests fail rather than skip without it.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, make_url, text
from sqlalchemy.orm import Session, sessionmaker

from orders_stock.api.app import create_app
from orders_stock.config import Settings
from orders_stock.db import make_engine, make_session_factory

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def _test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise RuntimeError("TEST_DATABASE_URL must be set (see .env.example)")
    database = make_url(url).database or ""
    if not database.endswith("_test"):
        raise RuntimeError(f"Refusing to run tests against non-test database: {url}")
    return url


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(_env_file=None, database_url=_test_database_url())


@pytest.fixture(scope="session")
def alembic_config(settings: Settings) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    return config


@pytest.fixture(scope="session")
def engine(settings: Settings, alembic_config: Config) -> Iterator[Engine]:
    command.upgrade(alembic_config, "head")

    engine = make_engine(settings.database_url)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables(engine: Engine) -> None:
    tables = [t for t in inspect(engine).get_table_names() if t != "alembic_version"]
    if not tables:
        return
    quoted = ", ".join(f'"{t}"' for t in tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        yield client
