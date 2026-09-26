import os

from sqlalchemy import Connection, create_engine, pool

from alembic import context
from app.db import Base
from app.persistence import (  # noqa: F401
    achievements,
    gamification,
    identity,
    scenarios,
    sessions,
)

target_metadata = Base.metadata


def run_online(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=os.environ["DATABASE_URL"],
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif (connection := context.config.attributes.get("connection")) is not None:
    run_online(connection)
else:
    engine = create_engine(os.environ["DATABASE_URL"], poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            run_online(connection)
    finally:
        engine.dispose()
