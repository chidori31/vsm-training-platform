from dataclasses import dataclass
from datetime import datetime

from .common import DomainError, require_text, utc_time


@dataclass(frozen=True, slots=True)
class Notification:
    id: str
    employee_id: str
    title: str
    body: str
    created_at: datetime
    read_at: datetime | None = None

    def __post_init__(self) -> None:
        for field, value in (
            ("notification id", self.id),
            ("employee_id", self.employee_id),
            ("title", self.title),
            ("body", self.body),
        ):
            require_text(value, field)
        object.__setattr__(self, "created_at", utc_time(self.created_at, "created_at"))
        if self.read_at is not None:
            object.__setattr__(self, "read_at", utc_time(self.read_at, "read_at"))
            if self.read_at < self.created_at:
                raise DomainError("Notification cannot be read before creation")
