import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool, text

from fluxus.config import get_settings
from fluxus.db import Base
from fluxus import models  # noqa: F401  (enregistre les tables)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# configparser interprète `%` : on l'échappe pour les mots de passe qui en contiennent.
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        # Verrou consultatif : deux instances qui démarrent en même temps ne migrent pas en parallèle.
        await connection.execute(text("SELECT pg_advisory_lock(727001)"))
        try:
            await connection.run_sync(do_run_migrations)
            await connection.commit()
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(727001)"))
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
