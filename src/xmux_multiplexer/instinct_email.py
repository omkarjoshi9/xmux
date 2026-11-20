"""Email transport boundary for Instinct; concrete mailbox APIs are injected."""

from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from typing import Protocol

from .contracts import AgentCapabilities, AgentResult, AgentState, AgentTask, RemoteTaskReceipt


class DeliveryUncertain(RuntimeError):
    """Mailbox send may have succeeded; reconcile before attempting another send."""


class DeliveryState(StrEnum):
    ACCEPTED = "accepted"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class OutboundEmail:
    correlation_id: str
    recipient: str
    subject: str
    body: str


@dataclass(frozen=True)
class SendReceipt:
    delivery_id: str
    state: DeliveryState


@dataclass(frozen=True)
class InboundEmail:
    message_id: str
    sender: str
    correlation_id: str
    state: AgentState
    result_refs: tuple[str, ...] = ()


class Mailbox(Protocol):
    """Implementations send and fetch; sender verification is supplied separately."""

    def send(self, message: OutboundEmail) -> SendReceipt: ...
    def replies(self, correlation_id: str) -> tuple[InboundEmail, ...]: ...


class SenderVerifier(Protocol):
    def is_verified(self, sender: str) -> bool: ...


class InstinctEmailAdapter:
    id = "instinct-email"

    def __init__(self, mailbox: Mailbox, verifier: SenderVerifier, recipient: str) -> None:
        self._mailbox, self._verifier, self._recipient = mailbox, verifier, recipient
        self._lock = RLock()
        self._deliveries: dict[str, SendReceipt | DeliveryUncertain] = {}
        self._results: dict[str, AgentResult] = {}
        self._seen: set[tuple[str, str]] = set()
        self._tasks: dict[str, AgentTask] = {}

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(frozenset({"email_task"}), supports_status=True)

    def health(self) -> str:
        return "configured"  # No mailbox health API is assumed.

    def submit(self, task: AgentTask) -> RemoteTaskReceipt:
        correlation = f"{task.tenant_id}:{task.task_id}:{task.idempotency_key}"
        with self._lock:
            if correlation not in self._deliveries:
                self._tasks[correlation] = task
                message = OutboundEmail(correlation, self._recipient, f"XMUX task {correlation}", task.objective)
                try:
                    receipt = self._mailbox.send(message)
                except Exception as exc:
                    self._deliveries[correlation] = DeliveryUncertain("delivery_uncertain")
                    raise DeliveryUncertain("delivery_uncertain; reconcile mailbox before retry") from exc
                self._deliveries[correlation] = receipt
            delivery = self._deliveries[correlation]
            if isinstance(delivery, DeliveryUncertain) or delivery.state == DeliveryState.UNCERTAIN:
                self._deliveries[correlation] = DeliveryUncertain("delivery_uncertain")
                raise DeliveryUncertain("delivery_uncertain; reconcile mailbox before retry")
            return RemoteTaskReceipt(self.id, correlation, AgentState.RUNNING)

    def status(self, remote_task_id: str) -> AgentState:
        with self._lock:
            if remote_task_id not in self._deliveries:
                raise KeyError(remote_task_id)
            self._collect(remote_task_id)
            return self._results.get(remote_task_id, AgentResult(AgentState.RUNNING)).state

    def result(self, remote_task_id: str) -> AgentResult | None:
        with self._lock:
            if remote_task_id not in self._deliveries:
                raise KeyError(remote_task_id)
            self._collect(remote_task_id)
            return self._results.get(remote_task_id)

    def _collect(self, correlation: str) -> None:
        if isinstance(self._deliveries[correlation], DeliveryUncertain):
            return
        for reply in self._mailbox.replies(correlation):
            key = (correlation, reply.message_id)
            if key in self._seen:
                continue
            self._seen.add(key)
            if reply.correlation_id != correlation or not self._verifier.is_verified(reply.sender):
                continue
            candidate = AgentResult(reply.state, reply.result_refs)
            existing = self._results.get(correlation)
            if existing is None or (existing.state == AgentState.RUNNING and candidate.state != AgentState.RUNNING):
                self._results[correlation] = candidate
