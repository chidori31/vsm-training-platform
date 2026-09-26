import json
import math
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.domain.common import DomainError

EVENT_ID = UUID("04a6dd4e-c8f7-4d56-8411-9e06c36ccf60")
NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)


def event(**changes):
    from app.domain.events import DomainEvent

    return DomainEvent(
        **{
            "event_id": EVENT_ID,
            "event_type": "ExampleConfirmed",
            "occurred_at": NOW,
            "received_at": NOW + timedelta(seconds=2),
            "source": "synthetic-adapter",
            "subject_id": "synthetic-subject",
            "payload": {"count": 1},
        }
        | changes
    )


def test_domain_event_normalizes_utc_without_assuming_arrival_order():
    future = NOW + timedelta(days=1)
    result = event(occurred_at=future.astimezone(timezone(timedelta(hours=3))))
    assert result.occurred_at == future and result.occurred_at.tzinfo is UTC
    assert result.received_at < result.occurred_at
    assert result.schema_version == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_id", str(EVENT_ID)),
        ("event_id", None),
        ("event_type", ""),
        ("event_type", " " * 2),
        ("event_type", "x" * 129),
        ("source", ""),
        ("source", "x" * 129),
        ("source", "bad\x00source"),
        ("subject_id", " "),
        ("subject_id", "x" * 129),
        ("occurred_at", NOW.replace(tzinfo=None)),
        ("received_at", "2026-09-26"),
        ("schema_version", True),
        ("schema_version", 0),
        ("schema_version", 1.5),
    ],
)
def test_domain_event_rejects_invalid_envelope(field, value):
    with pytest.raises(DomainError):
        event(**{field: value})


@pytest.mark.parametrize("field", ["event_type", "source", "subject_id"])
def test_invalid_utf8_envelope_text_is_rejected_before_receipt(field):
    with pytest.raises(DomainError):
        event(**{field: "bad\ud800"})


def test_payload_is_a_defensive_deeply_immutable_copy():
    payload = {"items": [{"name": "original", "flags": [True, None]}]}
    result = event(payload=payload)
    before = result.fingerprint()
    payload["items"][0]["name"] = "changed"
    payload["items"][0]["flags"].append(False)
    assert result.payload["items"][0]["name"] == "original"
    assert result.payload["items"][0]["flags"] == (True, None)
    with pytest.raises(TypeError):
        result.payload["new"] = 1
    with pytest.raises(TypeError):
        result.payload["items"][0]["name"] = "mutable"
    with pytest.raises(FrozenInstanceError):
        result.source = "changed"
    assert result.fingerprint() == before


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        {1: "invalid key"},
        {"v": {1, 2}},
        {"v": object()},
        {"v": math.nan},
        {"v": math.inf},
        {"v": -math.inf},
        {"v": b"bytes"},
        {"v": "x" * (64 * 1024)},
    ],
)
def test_malformed_or_unbounded_json_payload_is_rejected(payload):
    with pytest.raises(DomainError):
        event(payload=payload)


def test_cyclic_and_excessively_deep_payloads_are_rejected():
    cycle = {}
    cycle["self"] = cycle
    array = []
    array.append(array)
    deep = {}
    for _ in range(18):
        deep = {"child": deep}
    for payload in (cycle, {"array": array}, deep):
        with pytest.raises(DomainError):
            event(payload=payload)


def test_shared_acyclic_objects_are_allowed_without_aliasing():
    shared = {"value": 1}
    result = event(payload={"a": shared, "b": shared})
    shared["value"] = 9
    assert result.payload["a"]["value"] == result.payload["b"]["value"] == 1


def test_aggregate_budget_stops_before_a_later_sentinel_node():
    class Sentinel(Mapping):
        def __len__(self):
            return 1

        def __iter__(self):
            raise AssertionError("Traversal continued after exceeding the budget")

        def __getitem__(self, key):
            raise AssertionError("The later sentinel must not be traversed")

    shared = ["x" * 20_000]
    # The over-limit prefix is small; the guard prevents any expansive test input.
    with pytest.raises(DomainError, match="64 KiB"):
        event(payload={"items": [shared, shared, shared, shared, Sentinel()]})


def test_alias_expansion_is_bounded_during_traversal():
    class CountedMapping(Mapping):
        visits = 0

        def __len__(self):
            return 1

        def __iter__(self):
            self.visits += 1
            if self.visits > 4:
                raise AssertionError("Repeated aliases were expanded past the budget")
            return iter(("value",))

        def __getitem__(self, key):
            return "x" * 20_000

    shared = CountedMapping()
    with pytest.raises(DomainError, match="64 KiB"):
        event(payload={"items": [shared] * 10_000})
    assert 1 <= shared.visits <= 4


@pytest.mark.parametrize(
    "content",
    [
        [None, True, False, 123, -456, 1.25, -0.0],
        {"emoji": "🚄", "кириллица": "текст", "escapes": '\n\x00"\\'},
        [{}, [], [1, {"nested": [False, None]}]],
    ],
)
def test_exact_canonical_payload_byte_boundary(content):
    from app.domain.events import MAX_PAYLOAD_BYTES

    payload = {"content": content, "padding": ""}
    overhead = len(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    )
    payload["padding"] = "x" * (MAX_PAYLOAD_BYTES - overhead)
    assert event(payload=payload).payload["padding"] == payload["padding"]
    payload["padding"] += "x"
    with pytest.raises(DomainError, match="64 KiB"):
        event(payload=payload)


def test_fingerprint_ignores_transport_time_and_object_order():
    original = event(payload={"b": [True, None], "a": {"value": 2}})
    redelivery = event(
        received_at=NOW + timedelta(days=2),
        occurred_at=NOW.astimezone(timezone(timedelta(hours=3))),
        payload={"a": {"value": 2}, "b": [True, None]},
    )
    assert original.fingerprint() == redelivery.fingerprint()
    assert len(original.fingerprint()) == 64


@pytest.mark.parametrize(
    "changes",
    [
        {"event_id": UUID("06934d22-0b3c-4d2b-93ef-e237f3151c22")},
        {"event_type": "DifferentEvent"},
        {"source": "other-adapter"},
        {"subject_id": "other-subject"},
        {"occurred_at": NOW + timedelta(seconds=1)},
        {"schema_version": 2},
        {"payload": {"count": 2}},
    ],
)
def test_fingerprint_changes_with_semantic_identity(changes):
    assert event().fingerprint() != event(**changes).fingerprint()


def test_pending_client_contract_is_distinct_even_when_status_says_confirmed():
    from app.domain.events import DomainEvent, PendingClientEvent, SyncStatus

    pending = PendingClientEvent(
        client_event_id=EVENT_ID,
        event_type="ExampleReported",
        occurred_at=NOW,
        created_at=NOW,
        payload={"claim": ["unverified"]},
    )
    assert pending.sync_status is SyncStatus.PENDING and pending.attempts == 0
    assert pending.payload["claim"] == ("unverified",)
    assert not isinstance(pending, DomainEvent)
    confirmed_label = PendingClientEvent(
        client_event_id=EVENT_ID,
        event_type="ExampleReported",
        occurred_at=NOW,
        created_at=NOW,
        payload={},
        sync_status=SyncStatus.CONFIRMED,
    )
    assert not isinstance(confirmed_label, DomainEvent)


@pytest.mark.parametrize(
    "changes",
    [
        {"client_event_id": str(EVENT_ID)},
        {"event_type": ""},
        {"occurred_at": NOW.replace(tzinfo=None)},
        {"created_at": None},
        {"payload": {"v": math.nan}},
        {"sync_status": "confirmed"},
        {"attempts": True},
        {"attempts": -1},
        {"attempts": 1.2},
        {"schema_version": True},
        {"schema_version": 0},
    ],
)
def test_pending_client_contract_validates_without_promoting_trust(changes):
    from app.domain.events import PendingClientEvent

    values = dict(
        client_event_id=EVENT_ID,
        event_type="ExampleReported",
        occurred_at=NOW,
        created_at=NOW,
        payload={},
    )
    with pytest.raises(DomainError):
        PendingClientEvent(**(values | changes))
