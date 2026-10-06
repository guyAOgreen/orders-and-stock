from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from orders_stock.config import get_settings
from orders_stock.db import Base

config = context.config

if config.config_file_name is not None:
    # Keep application loggers alive when migrations run inside a process
    # (for example the test suite); fileConfig disables them by default.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# The URL comes from the application settings unless the caller has already
# set one on the Config (the test suite points it at the test database).
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

# Autogenerate only sees tables whose model modules have been imported. Import
# every module that defines mapped models here as they are added.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit the migration SQL without connecting to a database."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run the migrations against the configured database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
