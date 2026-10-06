from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker


def test_test_database_is_migrated_to_head(engine: Engine) -> None:
    with engine.connect() as connection:
        versions = (
            connection.execute(text("SELECT version_num FROM alembic_version"))
            .scalars()
            .all()
        )

    assert len(versions) == 1


def test_sessions_use_the_test_database(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        database = session.execute(text("SELECT current_database()")).scalar_one()

    assert database.endswith("_test")


def test_committed_work_is_visible_from_another_connection(
    engine: Engine, session_factory: sessionmaker[Session]
) -> None:
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE probe (n integer)"))
    try:
        with session_factory() as session:
            session.execute(text("INSERT INTO probe (n) VALUES (1)"))
            session.commit()

        with engine.connect() as other:
            count = other.execute(text("SELECT count(*) FROM probe")).scalar_one()

        assert count == 1
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE probe"))
