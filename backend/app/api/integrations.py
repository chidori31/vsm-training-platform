from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.application.errors import UseCaseError

Text = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"\S")]
Score = Annotated[int, Field(ge=0, le=100)]


class CompetencyExport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    competency_id: Text
    value: int


class TrainingResultExport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contract_version: Annotated[int, Field(ge=1, le=1)]
    event_id: Text
    employee_reference: Text
    session_id: Text
    scenario_id: Text
    scenario_version: Annotated[int, Field(gt=0)]
    # Pydantic accepts an ISO timestamp from JSON with strict=False on this field.
    completed_at: Annotated[AwareDatetime, Field(strict=False)]
    passenger_loyalty: Score
    safety_rating: Score
    competencies: Annotated[list[CompetencyExport], Field(max_length=128)]

    @model_validator(mode="after")
    def unique_competencies(self) -> Self:
        keys = [item.competency_id for item in self.competencies]
        if len(keys) != len(set(keys)):
            raise ValueError("Competency identifiers must be unique")
        return self


class IntegrationContract(BaseModel):
    status: Literal["contract_only"] = "contract_only"
    contract_version: Literal[1] = 1
    direction: Literal["platform_to_hr_lms"] = "platform_to_hr_lms"
    payload_schema: dict[str, Any]


router = APIRouter(prefix="/integrations/hr-lms", tags=["integration contracts"])


@router.get("/contract", response_model=IntegrationContract)
def contract() -> IntegrationContract:
    return IntegrationContract(payload_schema=TrainingResultExport.model_json_schema())


@router.post("/training-results", status_code=501)
def training_result_contract(payload: TrainingResultExport) -> None:
    raise UseCaseError(
        "integration_not_configured",
        "Contract only: no HR/LMS adapter is configured; nothing was sent or stored",
    )
