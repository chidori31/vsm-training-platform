from dataclasses import dataclass

from .common import (
    DomainError,
    freeze_items,
    require_integer,
    require_text,
    require_unique,
)
from .rules import Condition
from .scoring import AddScore, Metric


@dataclass(frozen=True, slots=True)
class Choice:
    id: str
    text: str
    target_node_id: str
    condition: Condition = Condition()
    effects: tuple[AddScore, ...] = ()

    def __post_init__(self) -> None:
        require_text(self.id, "choice id")
        require_text(self.text, "choice text")
        require_text(self.target_node_id, "target_node_id")
        if not isinstance(self.condition, Condition):
            raise DomainError("Expected Condition")
        object.__setattr__(self, "effects", freeze_items(self.effects, AddScore))


@dataclass(frozen=True, slots=True)
class ScenarioNode:
    id: str
    text: str
    choices: tuple[Choice, ...] = ()
    terminal: bool = False
    time_limit_seconds: int | None = None
    timeout_choice_id: str | None = None

    def __post_init__(self) -> None:
        require_text(self.id, "node id")
        require_text(self.text, "node text")
        choices = freeze_items(self.choices, Choice)
        object.__setattr__(self, "choices", choices)
        require_unique((choice.id for choice in choices), "choice id")
        if type(self.terminal) is not bool or self.terminal == bool(choices):
            raise DomainError(
                "Terminal nodes have no choices; other nodes require choices"
            )
        if self.time_limit_seconds is None:
            if self.timeout_choice_id is not None:
                raise DomainError("Timeout choice requires a time limit")
        else:
            require_integer(
                self.time_limit_seconds, "time_limit_seconds", positive=True
            )
            timeout = next(
                (choice for choice in choices if choice.id == self.timeout_choice_id),
                None,
            )
            if timeout is None or timeout.condition != Condition():
                raise DomainError("Timed node requires an unconditional timeout choice")


@dataclass(frozen=True, slots=True)
class Scenario:
    id: str
    version: int
    title: str
    start_node_id: str
    nodes: tuple[ScenarioNode, ...]
    competency_ids: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        require_text(self.id, "scenario id")
        require_text(self.title, "scenario title")
        require_text(self.start_node_id, "start_node_id")
        require_integer(self.version, "scenario version", positive=True)
        require_integer(self.schema_version, "schema_version", positive=True)
        if self.schema_version != 1:
            raise DomainError("Unsupported scenario schema version")
        nodes = freeze_items(self.nodes, ScenarioNode)
        competencies = tuple(self.competency_ids)
        require_unique(competencies, "competency id")
        require_unique((node.id for node in nodes), "node id")
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "competency_ids", competencies)
        by_id = {node.id: node for node in nodes}
        if self.start_node_id not in by_id:
            raise DomainError("Unknown start node")
        reverse: dict[str, set[str]] = {node.id: set() for node in nodes}
        for node in nodes:
            for choice in node.choices:
                if choice.target_node_id not in by_id:
                    raise DomainError("Unknown target node")
                reverse[choice.target_node_id].add(node.id)
                metrics = [effect.metric for effect in choice.effects]
                metrics.extend(
                    predicate.metric for predicate in choice.condition.predicates
                )
                if any(
                    metric.metric is Metric.COMPETENCY
                    and metric.competency_id not in competencies
                    for metric in metrics
                ):
                    raise DomainError("Undeclared competency")
        visited: set[str] = set()
        pending = [self.start_node_id]
        while pending:
            current = pending.pop()
            if current not in visited:
                visited.add(current)
                pending.extend(
                    choice.target_node_id for choice in by_id[current].choices
                )
        if visited != set(by_id):
            raise DomainError("Unreachable scenario nodes")
        can_finish: set[str] = set()
        pending = [node.id for node in nodes if node.terminal]
        while pending:
            current = pending.pop()
            if current not in can_finish:
                can_finish.add(current)
                pending.extend(reverse[current])
        if can_finish != set(by_id):
            raise DomainError("Every node must have a structural path to a terminal")

    def node(self, node_id: str) -> ScenarioNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise DomainError("Unknown node")
