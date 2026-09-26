from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.domain.common import DomainError

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)
EVENT_ID = UUID("7f685ee0-b3d3-4ffb-8569-797a34118061")


def event(**changes):
    from app.domain.events import DomainEvent

    return DomainEvent(
        **(
            dict(
                event_id=EVENT_ID,
                event_type="ExampleConfirmed",
                source="synthetic-adapter",
                subject_id="subject",
                occurred_at=NOW,
                received_at=NOW,
                payload={},
            )
            | changes
        )
    )


class ExampleRule:
    def evaluate(self, event):
        from app.domain.reward_ledger import RewardGrant

        return RewardGrant(
            amount=7,
            unit="demo-points",
            reason="Synthetic example",
            rule_id="example-v1",
        )


class UnexpectedEvaluation:
    def evaluate(self, event):
        raise AssertionError("A duplicate must return its recorded outcome")


def test_earn_uses_verified_subject_first_receipt_time_and_predictable_balance():
    from app.domain.reward_ledger import EventState, RewardType, apply_event

    initial = EventState()
    source = event(payload={"amount": 999, "subject_id": "forged"})
    result = apply_event(initial, source, rule=ExampleRule())
    assert initial == EventState()
    assert result.receipts == (source,)
    transaction = result.transactions[0]
    assert transaction.subject_id == "subject"
    assert transaction.kind is RewardType.EARN
    assert transaction.amount == 7 and transaction.source_event_id == EVENT_ID
    assert transaction.created_at == NOW and transaction.rule_id == "example-v1"
    assert result.balance("subject", "demo-points") == 7
    assert result.balance("forged", "demo-points") == 0
    assert result.balance("subject", "other-unit") == 0
    assert apply_event(EventState(), source, rule=ExampleRule()) == result


def test_duplicate_and_transport_redelivery_skip_rule_and_grant_once():
    from app.domain.reward_ledger import EventState, apply_event

    state = apply_event(EventState(), event(), rule=ExampleRule())
    assert apply_event(state, event(), rule=UnexpectedEvaluation()) is state
    assert (
        apply_event(
            state,
            event(received_at=NOW + timedelta(days=1)),
            rule=UnexpectedEvaluation(),
        )
        is state
    )
    assert state.balance("subject", "demo-points") == 7
    assert len(state.transactions) == len(state.receipts) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"payload": {"different": True}},
        {"subject_id": "different"},
        {"source": "different"},
        {"event_type": "DifferentConfirmed"},
        {"schema_version": 2},
        {"occurred_at": NOW + timedelta(seconds=1)},
    ],
)
def test_reused_event_id_with_changed_semantics_is_rejected(changes):
    from app.domain.reward_ledger import EventState, apply_event

    state = apply_event(EventState(), event(), rule=ExampleRule())
    with pytest.raises(DomainError):
        apply_event(state, event(**changes), rule=UnexpectedEvaluation())
    assert len(state.transactions) == 1


def test_no_effect_event_is_recorded_and_never_recomputed_on_replay():
    from app.domain.reward_ledger import EventState, apply_event

    class NoGrant:
        def evaluate(self, event):
            return None

    state = apply_event(EventState(), event(), rule=NoGrant())
    assert state.receipts == (event(),) and state.transactions == ()
    assert apply_event(state, event(), rule=UnexpectedEvaluation()) is state


def test_different_event_ids_accumulate_distinct_immutable_transactions():
    from app.domain.reward_ledger import EventState, apply_event

    first = apply_event(EventState(), event(), rule=ExampleRule())
    second = apply_event(
        first,
        event(event_id=UUID("b3f2f506-e1d9-4c25-a8ad-6f29e14b0cc5")),
        rule=ExampleRule(),
    )
    assert second.balance("subject", "demo-points") == 14
    assert first.balance("subject", "demo-points") == 7
    assert len({t.id for t in second.transactions}) == 2
    with pytest.raises(FrozenInstanceError):
        second.transactions[0].amount = 100
    with pytest.raises(FrozenInstanceError):
        second.receipts = ()


def test_rule_failure_leaves_original_receipts_and_ledger_untouched():
    from app.domain.reward_ledger import EventState, apply_event

    class Broken:
        def evaluate(self, event):
            raise RuntimeError("Synthetic failure")

    initial = EventState()
    with pytest.raises(RuntimeError, match="Synthetic failure"):
        apply_event(initial, event(), rule=Broken())
    assert initial.receipts == initial.transactions == ()
    assert len(apply_event(initial, event(), rule=ExampleRule()).transactions) == 1


@pytest.mark.parametrize("status", ["PENDING", "CONFIRMED"])
def test_client_event_cannot_enter_reward_processing_even_if_labelled_confirmed(status):
    from app.domain.events import PendingClientEvent, SyncStatus
    from app.domain.reward_ledger import EventState, apply_event

    pending = PendingClientEvent(
        client_event_id=EVENT_ID,
        event_type="ExampleReported",
        occurred_at=NOW,
        created_at=NOW,
        payload={},
        sync_status=SyncStatus[status],
    )
    with pytest.raises(DomainError):
        apply_event(EventState(), pending, rule=UnexpectedEvaluation())


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": True},
        {"amount": 0},
        {"amount": -1},
        {"amount": 1.2},
        {"unit": ""},
        {"unit": "x" * 33},
        {"reason": " "},
        {"reason": "x" * 513},
        {"rule_id": ""},
        {"rule_id": "x" * 129},
    ],
)
def test_grants_validate_strict_amounts_and_bounded_explanations(changes):
    from app.domain.reward_ledger import RewardGrant

    with pytest.raises(DomainError):
        RewardGrant(
            **(
                dict(
                    amount=1,
                    unit="demo-points",
                    reason="Synthetic example",
                    rule_id="example-v1",
                )
                | changes
            )
        )


@pytest.mark.parametrize("field", ["unit", "reason", "rule_id"])
def test_invalid_utf8_grant_text_is_rejected(field):
    from app.domain.reward_ledger import RewardGrant

    values = dict(
        amount=1, unit="demo-points", reason="Synthetic", rule_id="example-v1"
    )
    with pytest.raises(DomainError):
        RewardGrant(**(values | {field: "bad\ud800"}))


@pytest.mark.parametrize("kind", ["SPEND", "ADJUSTMENT"])
def test_spend_and_adjustment_are_explicitly_unsupported(kind):
    from app.domain.reward_ledger import RewardTransaction, RewardType

    with pytest.raises(DomainError, match="unsupported"):
        RewardTransaction(
            id=UUID("f52211c1-898f-4629-afc0-7a1f3e03e64f"),
            subject_id="subject",
            kind=RewardType[kind],
            amount=1,
            unit="demo-points",
            reason="Synthetic",
            source_event_id=EVENT_ID,
            created_at=NOW,
            rule_id="example-v1",
        )


def test_transaction_constructor_validates_types_and_identifiers():
    from app.domain.reward_ledger import RewardTransaction, RewardType

    values = dict(
        id=UUID("f52211c1-898f-4629-afc0-7a1f3e03e64f"),
        subject_id="subject",
        kind=RewardType.EARN,
        amount=1,
        unit="demo-points",
        reason="Synthetic",
        source_event_id=EVENT_ID,
        created_at=NOW,
        rule_id="example-v1",
    )
    for invalid in [
        {"id": "not-a-uuid"},
        {"source_event_id": str(EVENT_ID)},
        {"subject_id": ""},
        {"kind": "earn"},
        {"amount": True},
        {"created_at": NOW.replace(tzinfo=None)},
    ]:
        with pytest.raises(DomainError):
            RewardTransaction(**(values | invalid))


def test_state_rejects_duplicate_receipts_transactions_and_forged_references():
    from app.domain.reward_ledger import EventState, apply_event

    state = apply_event(EventState(), event(), rule=ExampleRule())
    transaction = state.transactions[0]
    invalid_states = [
        dict(receipts=(event(), event())),
        dict(receipts=state.receipts, transactions=(transaction, transaction)),
        dict(transactions=state.transactions),
        dict(
            receipts=state.receipts,
            transactions=(replace(transaction, subject_id="other"),),
        ),
        dict(
            receipts=state.receipts,
            transactions=(replace(transaction, created_at=NOW + timedelta(seconds=1)),),
        ),
        dict(
            receipts=state.receipts,
            transactions=(
                replace(
                    transaction,
                    source_event_id=UUID("3fcd6a86-833f-4532-b4c7-ab3d850608a6"),
                ),
            ),
        ),
        dict(
            receipts=state.receipts,
            transactions=(
                replace(transaction, id=UUID("3fcd6a86-833f-4532-b4c7-ab3d850608a6")),
            ),
        ),
    ]
    for values in invalid_states:
        with pytest.raises(DomainError):
            EventState(**values)


def test_invalid_rule_result_does_not_mark_event_as_processed():
    from app.domain.reward_ledger import EventState, apply_event

    class InvalidRule:
        def evaluate(self, event):
            return {"amount": 999}

    initial = EventState()
    with pytest.raises(DomainError):
        apply_event(initial, event(), rule=InvalidRule())
    assert initial == EventState()
