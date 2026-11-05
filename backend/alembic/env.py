from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from app.config import get_config
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option("sqlalchemy.url", get_config().database_url.replace("%", "%%"))
target_metadata = Base.metadata

_LOCK_ID = 727274  # arbitrary app-wide advisory lock id


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
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
        is_pg = connection.dialect.name == "postgresql"
        if is_pg:
            # API and worker may boot simultaneously: serialize migrations
            connection.execute(text(f"SELECT pg_advisory_lock({_LOCK_ID})"))
            connection.commit()
        try:
            context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            if is_pg:
                connection.commit()
                connection.execute(text(f"SELECT pg_advisory_unlock({_LOCK_ID})"))
                connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
