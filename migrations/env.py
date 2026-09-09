from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from tradingagents.platform.compliance import models as compliance_models  # noqa: F401
from tradingagents.platform.connections import models as connection_models  # noqa: F401
from tradingagents.platform.identity import Base
from tradingagents.platform.jobs import models as job_models  # noqa: F401
from tradingagents.platform.llm_connections import models as llm_connection_models  # noqa: F401
from tradingagents.platform.portfolio import models as portfolio_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("TRADINGAGENTS_DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
elif not config.get_main_option("sqlalchemy.url"):
    raise RuntimeError("TRADINGAGENTS_DATABASE_URL is required for migrations")

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
