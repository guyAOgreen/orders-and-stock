from sqlalchemy import Engine, create_engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DEFAULT_CONNECT_TIMEOUT_SECONDS = 5


class Base(DeclarativeBase):
    """Declarative base shared by all mapped models."""


def make_engine(database_url: str) -> Engine:
    # Fail fast when PostgreSQL is unreachable instead of hanging on connect.
    # A `connect_timeout` query parameter in the URL takes precedence.
    url = make_url(database_url)
    if "connect_timeout" not in url.query:
        url = url.update_query_dict(
            {"connect_timeout": str(DEFAULT_CONNECT_TIMEOUT_SECONDS)}
        )
    return create_engine(url)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    # Callers own the transaction: nothing is committed unless they commit.
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
