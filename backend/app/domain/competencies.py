from dataclasses import dataclass

from .common import require_integer, require_text


@dataclass(frozen=True, slots=True)
class Competency:
    id: str
    name: str
    description: str

    def __post_init__(self) -> None:
        require_text(self.id, "competency id")
        require_text(self.name, "competency name")
        require_text(self.description, "competency description")


@dataclass(frozen=True, slots=True)
class CompetencyProgress:
    competency_id: str
    points: int

    def __post_init__(self) -> None:
        require_text(self.competency_id, "competency_id")
        require_integer(self.points, "competency points")
