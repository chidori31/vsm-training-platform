from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.domain.rules import Condition, ConditionMode, Operator, Predicate
from app.domain.scenario import Choice, Scenario, ScenarioNode
from app.domain.scoring import AddScore, Metric, MetricRef

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
PositiveInteger = Annotated[int, Field(gt=0, le=2147483647)]
TIMEOUT_CHOICE_ID = "__timeout__"


class DocumentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class MetricDocument(DocumentModel):
    metric: Literal["passenger_loyalty", "safety_rating", "competency"]
    competency_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_metric(self) -> Self:
        self.to_metric()
        return self

    def to_metric(self) -> MetricRef:
        return MetricRef(Metric(self.metric), self.competency_id)


class PredicateDocument(MetricDocument):
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    value: int

    def to_domain(self) -> Predicate:
        return Predicate(self.to_metric(), Operator(self.operator), self.value)


class ConditionDocument(DocumentModel):
    mode: Literal["all", "any"] = "all"
    predicates: tuple[PredicateDocument, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def validate_condition(self) -> Self:
        self.to_domain()
        return self

    def to_domain(self) -> Condition:
        return Condition(
            ConditionMode(self.mode),
            tuple(item.to_domain() for item in self.predicates),
        )


class EffectDocument(MetricDocument):
    type: Literal["add_score"]
    delta: int

    def to_domain(self) -> AddScore:
        return AddScore(self.to_metric(), self.delta)


class OutcomeDocument(DocumentModel):
    destination: Identifier
    explanation: Text
    effects: tuple[EffectDocument, ...] = Field(default=(), max_length=64)


class ChoiceDocument(OutcomeDocument):
    id: Identifier
    text: Text
    condition: ConditionDocument = ConditionDocument()

    def to_domain(self) -> Choice:
        return Choice(
            id=self.id,
            text=self.text,
            target_node_id=self.destination,
            condition=self.condition.to_domain(),
            effects=tuple(effect.to_domain() for effect in self.effects),
            explanation=self.explanation,
        )


class NodeDocument(DocumentModel):
    id: Identifier
    text: Text
    terminal: bool = False
    choices: tuple[ChoiceDocument, ...] = Field(default=(), max_length=32)
    time_limit_seconds: PositiveInteger | None = None
    timeout: OutcomeDocument | None = None

    @model_validator(mode="after")
    def validate_node(self) -> Self:
        if self.terminal and (self.choices or self.timeout or self.time_limit_seconds):
            raise ValueError("Terminal node cannot have choices or a timeout")
        if not self.terminal and not self.choices:
            raise ValueError("Nonterminal node requires player choices")
        if (self.time_limit_seconds is None) != (self.timeout is None):
            raise ValueError(
                "A time limit and a timeout branch must be provided together"
            )
        if self.timeout and any(
            choice.destination == self.timeout.destination for choice in self.choices
        ):
            raise ValueError("The timeout branch requires a separate destination")
        self.to_domain()
        return self

    def to_domain(self) -> ScenarioNode:
        choices = tuple(choice.to_domain() for choice in self.choices)
        if self.timeout is not None:
            choices += (
                Choice(
                    id=TIMEOUT_CHOICE_ID,
                    text="Время на решение истекло",
                    target_node_id=self.timeout.destination,
                    effects=tuple(
                        effect.to_domain() for effect in self.timeout.effects
                    ),
                    explanation=self.timeout.explanation,
                ),
            )
        return ScenarioNode(
            id=self.id,
            text=self.text,
            choices=choices,
            terminal=self.terminal,
            time_limit_seconds=self.time_limit_seconds,
            timeout_choice_id=TIMEOUT_CHOICE_ID if self.timeout else None,
        )


class ScenarioDocument(DocumentModel):
    schema_version: Annotated[int, Field(ge=1, le=1)]
    id: Identifier
    version: PositiveInteger
    title: Text
    cycle_policy: Literal["allow", "forbid"] = "forbid"
    start_node_id: Identifier
    competency_ids: tuple[Identifier, ...] = ()
    nodes: tuple[NodeDocument, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        scenario = self.to_domain()
        if self.cycle_policy == "forbid":
            # Kahn's algorithm includes timeout edges and avoids recursion limits.
            incoming = dict.fromkeys((node.id for node in scenario.nodes), 0)
            for node in scenario.nodes:
                for choice in node.choices:
                    incoming[choice.target_node_id] += 1
            by_id = {node.id: node for node in scenario.nodes}
            pending = [node_id for node_id, count in incoming.items() if count == 0]
            visited = 0
            while pending:
                visited += 1
                for choice in by_id[pending.pop()].choices:
                    incoming[choice.target_node_id] -= 1
                    if incoming[choice.target_node_id] == 0:
                        pending.append(choice.target_node_id)
            if visited != len(scenario.nodes):
                raise ValueError("Cycle forbidden by cycle_policy")
        return self

    def to_domain(self) -> Scenario:
        return Scenario(
            id=self.id,
            version=self.version,
            title=self.title,
            start_node_id=self.start_node_id,
            nodes=tuple(node.to_domain() for node in self.nodes),
            competency_ids=self.competency_ids,
            schema_version=self.schema_version,
        )
