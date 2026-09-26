from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.application.event_ingestion import (
    EventVerification,
    ingest_client_event,
)
from app.domain.common import DomainError
from app.domain.events import DomainEvent, PendingClientEvent, SyncStatus
from app.domain.reward_ledger import EventState, RewardGrant

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)
FACT_ID = UUID("6f3a6e22-a78e-4544-a879-6e2ee4e4d001")


def pending(**changes):
    event = PendingClientEvent(
        client_event_id=uuid4(),
        event_type="synthetic.action",
        occurred_at=NOW - timedelta(minutes=1),
        created_at=NOW,
        payload={"claimed_amount": 1000000, "claimed_subject": "someone-else"},
    )
    return replace(event, **changes)


class SyntheticRule:
    def __init__(self):
        self.calls = 0

    def evaluate(self, event):
        self.calls += 1
        return RewardGrant(
            amount=10,
            unit="test-points",
            reason="Synthetic fixture only",
            rule_id="synthetic-fixed-v1",
        )


class SyntheticVerifier:
    source = "synthetic-authority"

    def __init__(self, status=SyncStatus.CONFIRMED, **changes):
        self.status = status
        self.changes = changes

    def verify(self, event, *, authenticated_subject_id, received_at):
        if self.status != SyncStatus.CONFIRMED:
            return EventVerification(self.status, None, "Synthetic verification result")
        # The server fact is independent of all client claims and local UUIDs.
        fact = DomainEvent(
            event_id=FACT_ID,
            event_type="synthetic.confirmed",
            occurred_at=NOW - timedelta(minutes=2),
            received_at=received_at,
            source=self.source,
            subject_id=authenticated_subject_id,
            payload={"evidence": "synthetic-fixed-fact"},
            schema_version=1,
        )
        return EventVerification(
            SyncStatus.CONFIRMED,
            replace(fact, **self.changes),
            "Synthetic verified fact",
        )


def ingest(state, local, rule, verifier=None, received_at=NOW):
    return ingest_client_event(
        state,
        local,
        authenticated_subject_id="synthetic-user",
        received_at=received_at,
        verifier=verifier,
        rule=rule,
    )


def test_client_confirmed_status_is_not_a_trusted_reward_without_adapter():
    state, rule = EventState(), SyntheticRule()
    local = pending(sync_status=SyncStatus.CONFIRMED)
    result = ingest(state, local, rule)
    assert result.state is state
    assert result.confirmation.client_event_id == local.client_event_id
    assert result.confirmation.status == SyncStatus.REJECTED
    assert result.confirmation.server_event_id is None
    assert rule.calls == 0


@pytest.mark.parametrize("status", [SyncStatus.REJECTED, SyncStatus.RETRYABLE])
def test_unconfirmed_source_result_does_not_run_rules_or_record_receipt(status):
    state, rule = EventState(), SyntheticRule()
    result = ingest(state, pending(), rule, SyntheticVerifier(status))
    assert result.state is state
    assert result.confirmation.status == status
    assert result.confirmation.server_event_id is None
    assert rule.calls == 0


def test_only_verified_fact_and_server_rule_determine_subject_and_reward():
    state, rule = EventState(), SyntheticRule()
    local = pending()
    result = ingest(state, local, rule, SyntheticVerifier())
    assert result.confirmation.status == SyncStatus.CONFIRMED
    assert result.confirmation.server_event_id == FACT_ID
    assert result.confirmation.client_event_id != FACT_ID
    assert result.state.balance("synthetic-user", "test-points") == 10
    assert result.state.balance("someone-else", "test-points") == 0
    assert state.balance("synthetic-user", "test-points") == 0
    assert "claimed_amount" not in result.state.receipts[0].payload


def test_distinct_offline_ids_for_same_confirmed_fact_cannot_claim_twice():
    rule, verifier = SyntheticRule(), SyntheticVerifier()
    first = ingest(EventState(), pending(), rule, verifier)
    local_retry = pending(client_event_id=uuid4(), attempts=4)
    second = ingest(
        first.state, local_retry, rule, verifier, received_at=NOW + timedelta(days=1)
    )
    assert second.state == first.state
    assert second.confirmation.client_event_id == local_retry.client_event_id
    assert second.confirmation.server_event_id == FACT_ID
    assert len(second.state.transactions) == 1
    assert rule.calls == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "unconfigured-source"},
        {"subject_id": "another-user"},
        {"received_at": NOW + timedelta(seconds=1)},
    ],
)
def test_misbound_adapter_confirmation_is_rejected_before_effect(changes):
    state, rule = EventState(), SyntheticRule()
    with pytest.raises(DomainError):
        ingest(state, pending(), rule, SyntheticVerifier(**changes))
    assert not state.receipts and not state.transactions
    assert rule.calls == 0


@pytest.mark.parametrize("status", [SyncStatus.PENDING, SyncStatus.IN_FLIGHT])
def test_verification_response_requires_final_or_retryable_disposition(status):
    with pytest.raises(DomainError):
        EventVerification(status, None, "Not a server disposition")


def test_confirmed_response_cannot_contain_a_pending_client_event():
    with pytest.raises(DomainError):
        EventVerification(SyncStatus.CONFIRMED, pending(), "A claim is not evidence")


def test_confirmation_requires_event_and_rejection_cannot_smuggle_event():
    with pytest.raises(DomainError):
        EventVerification(SyncStatus.CONFIRMED, None, "Missing fact")
    fact = (
        SyntheticVerifier()
        .verify(pending(), authenticated_subject_id="synthetic-user", received_at=NOW)
        .event
    )
    with pytest.raises(DomainError):
        EventVerification(SyncStatus.REJECTED, fact, "Rejected")


def test_verifier_failure_does_not_acknowledge_or_change_state():
    class BrokenVerifier(SyntheticVerifier):
        def verify(self, *args, **kwargs):
            raise RuntimeError("Synthetic unavailable verifier")

    state, rule = EventState(), SyntheticRule()
    with pytest.raises(RuntimeError):
        ingest(state, pending(), rule, BrokenVerifier())
    assert not state.receipts and not state.transactions
    assert rule.calls == 0


@pytest.mark.parametrize("subject", ["", " ", "x" * 129, "bad\nsubject"])
def test_invalid_authenticated_context_rejected(subject):
    with pytest.raises(DomainError):
        ingest_client_event(
            EventState(),
            pending(),
            authenticated_subject_id=subject,
            received_at=NOW,
            verifier=SyntheticVerifier(),
            rule=SyntheticRule(),
        )


def test_naive_server_received_time_rejected():
    with pytest.raises(DomainError):
        ingest(
            EventState(),
            pending(),
            SyntheticRule(),
            received_at=NOW.replace(tzinfo=None),
        )


def test_invalid_utf8_authenticated_context_is_rejected_without_adapter():
    with pytest.raises(DomainError):
        ingest_client_event(
            EventState(),
            pending(),
            authenticated_subject_id="bad\ud800",
            received_at=NOW,
            verifier=None,
            rule=SyntheticRule(),
        )


def test_invalid_utf8_verification_reason_is_rejected():
    with pytest.raises(DomainError):
        EventVerification(SyncStatus.REJECTED, None, "bad\ud800")


def test_omitted_verifier_fails_closed_without_processing_event():
    state, rule = EventState(), SyntheticRule()
    result = ingest_client_event(
        state,
        pending(),
        authenticated_subject_id="synthetic-user",
        received_at=NOW,
        rule=rule,
    )
    assert result.state is state
    assert result.confirmation.status is SyncStatus.REJECTED
    assert rule.calls == 0
