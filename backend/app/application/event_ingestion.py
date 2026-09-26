"""Dormant reference ingestion boundary; no provider, endpoint or durable store.

A confirmation here is a candidate result of verification, not a durable delivery
acknowledgement. Live callers must atomically persist receipt and effects before
acknowledging. The current training application never calls this module.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.common import DomainError, utc_time
from app.domain.events import DomainEvent, PendingClientEvent, SyncStatus, bounded_text
from app.domain.reward_ledger import EventState, RewardRule, apply_event

_DISPOSITIONS = (SyncStatus.CONFIRMED, SyncStatus.REJECTED, SyncStatus.RETRYABLE)


@dataclass(frozen=True, slots=True)
class EventVerification:
    status: SyncStatus
    event: DomainEvent | None
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, SyncStatus) or self.status not in _DISPOSITIONS:
            raise DomainError("Expected a server verification disposition")
        bounded_text(self.reason, "verification reason", 512)
        if self.status is SyncStatus.CONFIRMED:
            if not isinstance(self.event, DomainEvent):
                raise DomainError("Confirmation requires a verified domain event")
        elif self.event is not None:
            raise DomainError("Unconfirmed result cannot contain a domain event")


class ClientEventVerifier(Protocol):
    """Server-configured authority, never selected or supplied by the client.

    Resolve a canonical fact ID and subject from independent evidence. Validate
    event type, schema, actor entitlement and occurrence time against the actual
    source contract. Local UUID/status/payload are claims, not that evidence.
    No production implementation is configured in this repository.
    """

    @property
    def source(self) -> str: ...

    def verify(
        self,
        pending: PendingClientEvent,
        *,
        authenticated_subject_id: str,
        received_at: datetime,
    ) -> EventVerification: ...


class AtomicEventStore(Protocol):
    """Required port for future live use; deliberately no in-memory fallback.

    Serialize concurrent transitions over the affected receipts/accounts, persist
    the resulting receipts AND effects in one durable transaction, then return.
    Exceptions roll back both; replay must read committed prior state. A process
    restart must preserve receipts. Transition code may not do external I/O.
    An implementation must not call the same rule on separate uncommitted states
    and claim exactly-once delivery. No SQL adapter is implemented yet.
    """

    def transact(
        self, transition: Callable[[EventState], EventState]
    ) -> EventState: ...


@dataclass(frozen=True, slots=True)
class SyncConfirmation:
    client_event_id: UUID
    status: SyncStatus
    server_event_id: UUID | None
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.client_event_id, UUID):
            raise DomainError("Expected client UUID")
        if not isinstance(self.status, SyncStatus) or self.status not in _DISPOSITIONS:
            raise DomainError("Expected a server confirmation disposition")
        if self.status is SyncStatus.CONFIRMED:
            if not isinstance(self.server_event_id, UUID):
                raise DomainError("Confirmed fact requires server event UUID")
        elif self.server_event_id is not None:
            raise DomainError("Unconfirmed claim cannot reference a confirmed fact")
        bounded_text(self.reason, "confirmation reason", 512)


@dataclass(frozen=True, slots=True)
class IngestionResult:
    state: EventState
    confirmation: SyncConfirmation


def ingest_client_event(
    state: EventState,
    pending: PendingClientEvent,
    *,
    authenticated_subject_id: str,
    received_at: datetime,
    verifier: ClientEventVerifier | None = None,
    rule: RewardRule,
) -> IngestionResult:
    """Reference verification flow. Does not persist or acknowledge over a network.

    Server context comes from the caller, not from a client body. The supplied
    rule is server-owned and chooses any reward; payload amounts are never posted
    automatically. A failed verification/rule leaves caller-owned state unchanged.
    """
    if not isinstance(state, EventState) or not isinstance(pending, PendingClientEvent):
        raise DomainError("Expected event state and an untrusted client claim")
    bounded_text(authenticated_subject_id, "authenticated subject", 128)
    received_at = utc_time(received_at, "received_at")
    verification = (
        EventVerification(
            SyncStatus.REJECTED, None, "No verification adapter configured"
        )
        if verifier is None
        else verifier.verify(
            pending,
            authenticated_subject_id=authenticated_subject_id,
            received_at=received_at,
        )
    )
    if not isinstance(verification, EventVerification):
        raise DomainError("Invalid verifier response")
    event = verification.event
    if event is None:
        return IngestionResult(
            state,
            SyncConfirmation(
                pending.client_event_id, verification.status, None, verification.reason
            ),
        )
    if (
        verifier is None
        or event.source != verifier.source
        or event.subject_id != authenticated_subject_id
        or event.received_at != received_at
    ):
        raise DomainError("Verified fact does not match server source/subject/receipt")
    updated = apply_event(state, event, rule=rule)
    return IngestionResult(
        updated,
        SyncConfirmation(
            pending.client_event_id,
            SyncStatus.CONFIRMED,
            event.event_id,
            verification.reason,
        ),
    )
