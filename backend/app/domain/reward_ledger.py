"""Dormant, sequential reference reducer for non-monetary earned points.

No receipt or reward is persisted here. A caller must atomically persist the
receipt and ledger transition with concurrency control before live activation.
Immutability of a Python value is not durable or concurrent exactly-once delivery.
Existing training rewards are independent and are not connected to this module.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid5

from .common import DomainError, freeze_items, require_integer, utc_time
from .events import DomainEvent, bounded_text


class RewardType(StrEnum):
    EARN = "earn"
    SPEND = "spend"
    ADJUSTMENT = "adjustment"


@dataclass(frozen=True, slots=True)
class RewardGrant:
    amount: int
    unit: str
    reason: str
    rule_id: str

    def __post_init__(self) -> None:
        require_integer(self.amount, "amount", positive=True)
        bounded_text(self.unit, "unit", 32)
        bounded_text(self.reason, "reason", 512)
        bounded_text(self.rule_id, "rule_id")


class RewardRule(Protocol):
    """Caller supplies an approved, side-effect-free policy; no default formula."""

    def evaluate(self, event: DomainEvent) -> RewardGrant | None: ...


@dataclass(frozen=True, slots=True)
class RewardTransaction:
    id: UUID
    subject_id: str
    kind: RewardType
    amount: int
    unit: str
    reason: str
    source_event_id: UUID
    created_at: datetime
    rule_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID) or not isinstance(self.source_event_id, UUID):
            raise DomainError("Transaction and source event identifiers must be UUIDs")
        bounded_text(self.subject_id, "subject_id")
        if not isinstance(self.kind, RewardType):
            raise DomainError("kind must be a RewardType")
        if self.kind is not RewardType.EARN:
            raise DomainError("SPEND and ADJUSTMENT posting is unsupported")
        RewardGrant(self.amount, self.unit, self.reason, self.rule_id)
        object.__setattr__(self, "created_at", utc_time(self.created_at, "created_at"))


def _transaction_id(event_id: UUID) -> UUID:
    # One reference outcome per event; changing a rule cannot create a second award.
    return uuid5(event_id, "reward-ledger:earn:v1")


@dataclass(frozen=True, slots=True)
class EventState:
    receipts: tuple[DomainEvent, ...] = ()
    transactions: tuple[RewardTransaction, ...] = ()

    def __post_init__(self) -> None:
        receipts = freeze_items(self.receipts, DomainEvent)
        transactions = freeze_items(self.transactions, RewardTransaction)
        by_id = {event.event_id: event for event in receipts}
        if len(by_id) != len(receipts):
            raise DomainError("Duplicate event receipt")
        if len({transaction.id for transaction in transactions}) != len(transactions):
            raise DomainError("Duplicate reward transaction")
        if len({t.source_event_id for t in transactions}) != len(transactions):
            raise DomainError("An event cannot grant a reward twice")
        for transaction in transactions:
            source = by_id.get(transaction.source_event_id)
            if (
                source is None
                or transaction.subject_id != source.subject_id
                or transaction.created_at != source.received_at
                or transaction.id != _transaction_id(source.event_id)
            ):
                raise DomainError("Reward transaction disagrees with its event receipt")
        object.__setattr__(self, "receipts", receipts)
        object.__setattr__(self, "transactions", transactions)

    def balance(self, subject_id: str, unit: str) -> int:
        bounded_text(subject_id, "subject_id")
        bounded_text(unit, "unit", 32)
        return sum(
            transaction.amount
            for transaction in self.transactions
            if transaction.subject_id == subject_id and transaction.unit == unit
        )


def apply_event(
    state: EventState, event: DomainEvent, *, rule: RewardRule
) -> EventState:
    """Return one immutable transition; receipts also remember no-effect outcomes.

    Only an already verified envelope may enter this boundary. Its Python type
    distinguishes pending client reports but cannot itself establish source trust.
    """
    if not isinstance(state, EventState) or not isinstance(event, DomainEvent):
        raise DomainError("Expected EventState and a verified DomainEvent")
    for receipt in state.receipts:
        if receipt.event_id == event.event_id:
            if receipt.fingerprint() != event.fingerprint():
                raise DomainError("Event id was already used for different content")
            return state
    grant = rule.evaluate(event)
    if grant is not None and not isinstance(grant, RewardGrant):
        raise DomainError("Reward policy must return RewardGrant or None")
    transactions = state.transactions
    if grant is not None:
        transactions += (
            RewardTransaction(
                _transaction_id(event.event_id),
                event.subject_id,
                RewardType.EARN,
                grant.amount,
                grant.unit,
                grant.reason,
                event.event_id,
                event.received_at,
                grant.rule_id,
            ),
        )
    return EventState(state.receipts + (event,), transactions)
