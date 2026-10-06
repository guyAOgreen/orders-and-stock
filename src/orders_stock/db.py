from sqlalchemy import Engine, MetaData, create_engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DEFAULT_CONNECT_TIMEOUT_SECONDS = 5

# Deterministic constraint names, so migrations can refer to them later and
# tests can assert which constraint rejected a row.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by all mapped models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


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
