"""Dormant event contracts; construction alone does not verify an external fact.

An application adapter must authenticate the source, resolve the subject and
validate the fact before using DomainEvent for rewards. Client sync labels are
informational and never confer that authority. No adapter or transport is wired.
"""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from uuid import UUID

from .common import DomainError, require_integer, require_text, utc_time

type JsonValue = (
    None | bool | int | float | str | tuple[JsonValue, ...] | Mapping[str, JsonValue]
)

MAX_PAYLOAD_DEPTH = 16
MAX_PAYLOAD_BYTES = 64 * 1024


def bounded_text(value: str, field: str, maximum: int = 128) -> None:
    require_text(value, field)
    if len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise DomainError(f"{field} exceeds its limit or contains control characters")
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise DomainError(f"{field} must be valid UTF-8 text") from error


@dataclass(slots=True)
class _PayloadBudget:
    remaining: int = MAX_PAYLOAD_BYTES

    def consume(self, size: int) -> None:
        if size > self.remaining:
            raise DomainError("Event payload exceeds 64 KiB of canonical UTF-8 JSON")
        self.remaining -= size


def _freeze_json(
    value: object, ancestors: set[int], depth: int, budget: _PayloadBudget
) -> JsonValue:
    if depth > MAX_PAYLOAD_DEPTH:
        raise DomainError("Event payload exceeds its maximum depth")
    if value is None or type(value) in {bool, int}:
        budget.consume(len(_canonical_json(value)))
        # Explicit branches preserve JSON booleans instead of treating them as ints.
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
    if type(value) is float:
        if not math.isfinite(value):
            raise DomainError("Event payload requires finite JSON numbers")
        budget.consume(len(_canonical_json(value)))
        return value
    if type(value) is str:
        if len(value) > MAX_PAYLOAD_BYTES:
            raise DomainError("Event payload exceeds its size limit")
        budget.consume(len(_canonical_json(value)))
        return value
    if not isinstance(value, (Mapping, list, tuple)):
        raise DomainError("Event payload must contain only JSON values")
    identity = id(value)
    if identity in ancestors:
        raise DomainError("Event payload must not contain cycles")
    if len(value) > MAX_PAYLOAD_BYTES:
        raise DomainError("Event payload exceeds its size limit")
    # Charge brackets/braces and all separators before descending. Every alias
    # consumes its full serialized size, so shared inputs cannot expand unboundedly.
    budget.consume(2 + max(0, len(value) - 1))
    ancestors.add(identity)
    try:
        if isinstance(value, Mapping):
            budget.consume(len(value))  # One colon per object member.
            result = {}
            for key, item in value.items():
                if type(key) is not str or len(key) > MAX_PAYLOAD_BYTES:
                    raise DomainError("Event payload requires bounded string keys")
                budget.consume(len(_canonical_json(key)))
                result[key] = _freeze_json(item, ancestors, depth + 1, budget)
            return MappingProxyType(result)
        return tuple(_freeze_json(item, ancestors, depth + 1, budget) for item in value)
    finally:
        ancestors.remove(identity)


def _plain_json(value: JsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    return value


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise DomainError("Event payload must be valid UTF-8 JSON") from error


def _payload(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise DomainError("Event payload must be a JSON object")
    frozen = _freeze_json(value, set(), 0, _PayloadBudget())
    if not isinstance(frozen, Mapping):
        raise DomainError("Event payload must be a JSON object")
    if len(_canonical_json(_plain_json(frozen))) > MAX_PAYLOAD_BYTES:
        raise DomainError("Event payload exceeds 64 KiB of canonical UTF-8 JSON")
    return frozen


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_id: UUID
    event_type: str
    occurred_at: datetime
    received_at: datetime
    source: str
    subject_id: str
    payload: Mapping[str, JsonValue]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, UUID):
            raise DomainError("event_id must be a UUID")
        for name in ("event_type", "source", "subject_id"):
            bounded_text(getattr(self, name), name)
        require_integer(self.schema_version, "schema_version", positive=True)
        object.__setattr__(
            self, "occurred_at", utc_time(self.occurred_at, "occurred_at")
        )
        object.__setattr__(
            self, "received_at", utc_time(self.received_at, "received_at")
        )
        object.__setattr__(self, "payload", _payload(self.payload))

    def fingerprint(self) -> str:
        """Stable content identity; delivery time is not part of the original fact."""
        identity = {
            "event_id": str(self.event_id),
            "event_type": self.event_type,
            "source": self.source,
            "subject_id": self.subject_id,
            "occurred_at": self.occurred_at.isoformat(),
            "schema_version": self.schema_version,
            "payload": _plain_json(self.payload),
        }
        return sha256(_canonical_json(identity)).hexdigest()


class SyncStatus(StrEnum):
    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    RETRYABLE = "retryable"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class PendingClientEvent:
    """Local queue contract, including untrusted copies of confirmation labels."""

    client_event_id: UUID
    event_type: str
    occurred_at: datetime
    created_at: datetime
    payload: Mapping[str, JsonValue]
    sync_status: SyncStatus = SyncStatus.PENDING
    attempts: int = 0
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.client_event_id, UUID):
            raise DomainError("client_event_id must be a UUID")
        bounded_text(self.event_type, "event_type")
        if not isinstance(self.sync_status, SyncStatus):
            raise DomainError("sync_status must be a SyncStatus")
        require_integer(self.attempts, "attempts")
        if self.attempts < 0:
            raise DomainError("attempts must not be negative")
        require_integer(self.schema_version, "schema_version", positive=True)
        object.__setattr__(
            self, "occurred_at", utc_time(self.occurred_at, "occurred_at")
        )
        object.__setattr__(self, "created_at", utc_time(self.created_at, "created_at"))
        object.__setattr__(self, "payload", _payload(self.payload))
