from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base shared by all mapped models."""


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    # Callers own the transaction: nothing is committed unless they commit.
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
