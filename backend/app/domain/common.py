from collections.abc import Iterable
from datetime import UTC, datetime


class DomainError(ValueError):
    """A domain value violates an invariant."""


def require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainError(f"{field} must be non-empty text")


def require_integer(value: int, field: str, *, positive: bool = False) -> None:
    if type(value) is not int or (positive and value <= 0):
        raise DomainError(
            f"{field} must be {'a positive ' if positive else 'an '}integer"
        )


def utc_time(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise DomainError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def require_unique(values: Iterable[str], field: str) -> None:
    items = tuple(values)
    for item in items:
        require_text(item, field)
    if len(items) != len(set(items)):
        raise DomainError(f"Duplicate {field}")


def freeze_items[T](values: Iterable[T], expected: type[T]) -> tuple[T, ...]:
    items = tuple(values)
    if any(not isinstance(item, expected) for item in items):
        raise DomainError(f"Expected {expected.__name__} items")
    return items
