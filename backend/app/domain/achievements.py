from dataclasses import dataclass
from datetime import datetime

from .common import DomainError, require_integer, require_text, utc_time
from .rules import Condition


@dataclass(frozen=True, slots=True)
class Achievement:
    id: str
    version: int
    name: str
    description: str
    condition: Condition

    def __post_init__(self) -> None:
        require_text(self.id, "achievement id")
        require_text(self.name, "achievement name")
        require_text(self.description, "achievement description")
        require_integer(self.version, "achievement version", positive=True)
        if not isinstance(self.condition, Condition):
            raise DomainError("Expected Condition")


@dataclass(frozen=True, slots=True)
class AchievementUnlock:
    id: str
    employee_id: str
    achievement_id: str
    achievement_version: int
    session_id: str
    unlocked_at: datetime

    def __post_init__(self) -> None:
        for field, value in (
            ("unlock id", self.id),
            ("employee_id", self.employee_id),
            ("achievement_id", self.achievement_id),
            ("session_id", self.session_id),
        ):
            require_text(value, field)
        require_integer(self.achievement_version, "achievement_version", positive=True)
        object.__setattr__(
            self, "unlocked_at", utc_time(self.unlocked_at, "unlocked_at")
        )
