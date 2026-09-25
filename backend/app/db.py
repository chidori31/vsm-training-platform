from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool


class Base(DeclarativeBase):
    """Shared metadata for future domain models and Alembic."""


def database_available(url: str | None) -> bool:
    if not url:
        return False
    try:
        engine = create_engine(
            url,
            poolclass=NullPool,
            connect_args={"connect_timeout": 3, "options": "-c statement_timeout=3000"},
        )
        try:
            with engine.connect() as connection:
                return bool(connection.scalar(text("SELECT 1")) == 1)
        finally:
            engine.dispose()
    except (SQLAlchemyError, ValueError):
        return False
