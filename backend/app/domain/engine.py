"""Deterministic transitions. The caller supplies IDs, time and trusted state."""

from dataclasses import replace
from datetime import datetime, timedelta

from .common import DomainError, require_integer, require_text, utc_time
from .gameplay import Decision, ScenarioSession, SessionStatus
from .scenario import Choice, Scenario, ScenarioNode
from .scoring import Metric, MetricRef, ScoreState, apply_effects


def start_session(
    scenario: Scenario,
    *,
    session_id: str,
    employee_id: str,
    initial_scores: ScoreState,
    now: datetime,
) -> ScenarioSession:
    required = {MetricRef(Metric.PASSENGER_LOYALTY), MetricRef(Metric.SAFETY_RATING)}
    required.update(
        MetricRef(Metric.COMPETENCY, key) for key in scenario.competency_ids
    )
    if (
        not isinstance(initial_scores, ScoreState)
        or set(initial_scores.values) != required
    ):
        raise DomainError(
            "Initial metrics must match loyalty, safety and declared competencies"
        )
    timestamp = utc_time(now, "now")
    terminal = scenario.node(scenario.start_node_id).terminal
    return ScenarioSession(
        id=session_id,
        employee_id=employee_id,
        scenario_id=scenario.id,
        scenario_version=scenario.version,
        current_node_id=scenario.start_node_id,
        scores=initial_scores,
        initial_scores=initial_scores,
        started_at=timestamp,
        status=SessionStatus.COMPLETED if terminal else SessionStatus.ACTIVE,
        completed_at=timestamp if terminal else None,
    )


def _entered_at(session: ScenarioSession) -> datetime:
    return session.decisions[-1].decided_at if session.decisions else session.started_at


def _deadline(node: ScenarioNode, session: ScenarioSession) -> datetime | None:
    if node.time_limit_seconds is None:
        return None
    try:
        return _entered_at(session) + timedelta(seconds=node.time_limit_seconds)
    except OverflowError as error:
        raise DomainError(
            "Node deadline exceeds the supported datetime range"
        ) from error


def _checked_time(session: ScenarioSession, now: datetime) -> datetime:
    timestamp = utc_time(now, "now")
    if timestamp < _entered_at(session):
        raise DomainError("Time precedes the latest session activity")
    return timestamp


def _transition(
    scenario: Scenario,
    session: ScenarioSession,
    *,
    node_id: str,
    choice_id: str | None,
    decision_id: str,
    expected_sequence: int,
    now: datetime,
    timeout: bool,
) -> ScenarioSession:
    """Apply one new event; used by both live commands and history replay."""
    if session.status is SessionStatus.COMPLETED:
        raise DomainError("Session is already completed")
    if expected_sequence != len(session.decisions):
        raise DomainError("Stale decision sequence")
    if node_id != session.current_node_id:
        raise DomainError("Decision does not target the current node")
    node = scenario.node(node_id)
    timestamp = _checked_time(session, now)
    deadline = _deadline(node, session)
    if timeout:
        if deadline is None or node.timeout_choice_id is None:
            raise DomainError("Current node has no timeout")
        if timestamp < deadline:
            raise DomainError("Cannot apply timeout before the deadline")
        choice_id = node.timeout_choice_id
    else:
        if choice_id == node.timeout_choice_id:
            raise DomainError("A timeout choice cannot be selected by a player")
        if deadline is not None and timestamp >= deadline:
            raise DomainError("Choice deadline has expired; process the timeout")
    choice = next((item for item in node.choices if item.id == choice_id), None)
    if choice is None:
        raise DomainError("Unknown choice for the current node")
    if not choice.condition.matches(session.scores):
        raise DomainError("Choice condition is not satisfied")
    scores = apply_effects(session.scores, choice.effects)
    destination = scenario.node(choice.target_node_id)
    decision = Decision(
        id=decision_id,
        session_id=session.id,
        node_id=node.id,
        choice_id=choice.id,
        sequence=len(session.decisions) + 1,
        decided_at=timestamp,
        effects=choice.effects,
        explanation=choice.explanation,
    )
    return replace(
        session,
        current_node_id=destination.id,
        scores=scores,
        status=SessionStatus.COMPLETED
        if destination.terminal
        else SessionStatus.ACTIVE,
        completed_at=timestamp if destination.terminal else None,
        decisions=session.decisions + (decision,),
    )


def restore_session(scenario: Scenario, session: ScenarioSession) -> ScenarioSession:
    """Replay trusted saved history and reject an inconsistent derived snapshot."""
    if (session.scenario_id, session.scenario_version) != (
        scenario.id,
        scenario.version,
    ):
        raise DomainError("Session requires its pinned scenario id and version")
    if session.initial_scores is None:
        raise DomainError("Session restoration requires initial_scores")
    restored = start_session(
        scenario,
        session_id=session.id,
        employee_id=session.employee_id,
        initial_scores=session.initial_scores,
        now=session.started_at,
    )
    for recorded in session.decisions:
        source = scenario.node(recorded.node_id)
        restored = _transition(
            scenario,
            restored,
            node_id=recorded.node_id,
            choice_id=recorded.choice_id,
            decision_id=recorded.id,
            expected_sequence=recorded.sequence - 1,
            now=recorded.decided_at,
            timeout=recorded.choice_id == source.timeout_choice_id,
        )
        if restored.decisions[-1] != recorded:
            raise DomainError(
                "Recorded decision differs from scenario effects or explanation"
            )
    if restored != session:
        raise DomainError("Saved session state differs from replayed history")
    return restored


def current_node(scenario: Scenario, session: ScenarioSession) -> ScenarioNode:
    return scenario.node(restore_session(scenario, session).current_node_id)


def node_deadline(scenario: Scenario, session: ScenarioSession) -> datetime | None:
    return _deadline(current_node(scenario, session), session)


def available_choices(
    scenario: Scenario, session: ScenarioSession, *, now: datetime
) -> tuple[Choice, ...]:
    node = current_node(scenario, session)
    timestamp = _checked_time(session, now)
    deadline = _deadline(node, session)
    if session.status is SessionStatus.COMPLETED or (
        deadline is not None and timestamp >= deadline
    ):
        return ()
    return tuple(
        choice
        for choice in node.choices
        if choice.id != node.timeout_choice_id
        and choice.condition.matches(session.scores)
    )


def _process(
    scenario: Scenario,
    session: ScenarioSession,
    *,
    node_id: str,
    choice_id: str | None,
    decision_id: str,
    expected_sequence: int,
    now: datetime,
    timeout: bool,
) -> ScenarioSession:
    require_text(node_id, "node_id")
    require_text(decision_id, "decision_id")
    if not timeout:
        if choice_id is None:
            raise DomainError("Choice id is required")
        require_text(choice_id, "choice_id")
    require_integer(expected_sequence, "expected_sequence")
    if expected_sequence < 0:
        raise DomainError("expected_sequence must not be negative")
    timestamp = utc_time(now, "now")
    restore_session(scenario, session)
    for recorded in session.decisions:
        if recorded.id == decision_id:
            was_timeout = (
                recorded.choice_id == scenario.node(recorded.node_id).timeout_choice_id
            )
            if (
                recorded.node_id != node_id
                or recorded.sequence != expected_sequence + 1
                or was_timeout != timeout
                or (not timeout and recorded.choice_id != choice_id)
            ):
                raise DomainError(
                    "Decision id was already used for a different command"
                )
            # A retry acknowledges the original event, even after later progress.
            # Its new processing time must not alter history, scores or deadlines.
            return session
    return _transition(
        scenario,
        session,
        node_id=node_id,
        choice_id=choice_id,
        decision_id=decision_id,
        expected_sequence=expected_sequence,
        now=timestamp,
        timeout=timeout,
    )


def advance(
    scenario: Scenario,
    session: ScenarioSession,
    *,
    node_id: str,
    choice_id: str,
    decision_id: str,
    expected_sequence: int,
    now: datetime,
) -> ScenarioSession:
    return _process(
        scenario,
        session,
        node_id=node_id,
        choice_id=choice_id,
        decision_id=decision_id,
        expected_sequence=expected_sequence,
        now=now,
        timeout=False,
    )


def expire(
    scenario: Scenario,
    session: ScenarioSession,
    *,
    node_id: str,
    decision_id: str,
    expected_sequence: int,
    now: datetime,
) -> ScenarioSession:
    return _process(
        scenario,
        session,
        node_id=node_id,
        choice_id=None,
        decision_id=decision_id,
        expected_sequence=expected_sequence,
        now=now,
        timeout=True,
    )
