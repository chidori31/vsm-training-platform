from dataclasses import dataclass

from .common import freeze_items, require_text, require_unique
from .competencies import CompetencyProgress


@dataclass(frozen=True, slots=True)
class EmployeeProfile:
    id: str
    display_name: str
    competencies: tuple[CompetencyProgress, ...] = ()

    def __post_init__(self) -> None:
        require_text(self.id, "employee id")
        require_text(self.display_name, "display_name")
        competencies = freeze_items(self.competencies, CompetencyProgress)
        require_unique((item.competency_id for item in competencies), "competency id")
        object.__setattr__(self, "competencies", competencies)
