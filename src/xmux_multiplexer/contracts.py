"""Provider-neutral task and adapter contracts."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping, Protocol


class AgentState(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class AgentCapabilities:
    operations: frozenset[str]
    supports_status: bool = True
    supports_cancel: bool = False
    supports_schedule: bool = False
    supports_memory_lookup: bool = False


@dataclass(frozen=True)
class AgentTask:
    task_id: str
    idempotency_key: str
    principal_id: str
    tenant_id: str
    intent: str
    objective: str
    required_capabilities: frozenset[str] = field(default_factory=frozenset)
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RemoteTaskReceipt:
    agent_id: str
    remote_task_id: str
    state: AgentState


@dataclass(frozen=True)
class AgentResult:
    state: AgentState
    result_refs: tuple[str, ...] = ()


class AgentAdapter(Protocol):
    id: str

    def capabilities(self) -> AgentCapabilities: ...
    def health(self) -> str: ...
    def submit(self, task: AgentTask) -> RemoteTaskReceipt: ...
    def status(self, remote_task_id: str) -> AgentState: ...
    def result(self, remote_task_id: str) -> AgentResult | None: ...
