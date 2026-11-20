"""Deterministic mock adapter for local integration tests."""

from hashlib import sha256

from .contracts import AgentCapabilities, AgentResult, AgentState, AgentTask, RemoteTaskReceipt


class MockAgent:
    def __init__(self, agent_id: str, operations: frozenset[str]) -> None:
        self.id = agent_id
        self._operations = operations
        self._tasks: dict[str, AgentTask] = {}

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(operations=self._operations, supports_status=True)

    def health(self) -> str:
        return "healthy"

    def submit(self, task: AgentTask) -> RemoteTaskReceipt:
        if not task.required_capabilities.issubset(self._operations):
            raise ValueError("unsupported_capability")
        token = sha256(
            f"{task.tenant_id}:{task.principal_id}:{task.idempotency_key}".encode()
        ).hexdigest()
        remote_id = f"{self.id}:{token}"
        previous = self._tasks.get(remote_id)
        if previous is not None and previous != task:
            raise ValueError("idempotency_conflict")
        self._tasks[remote_id] = task
        return RemoteTaskReceipt(self.id, remote_id, AgentState.COMPLETED)

    def status(self, remote_task_id: str) -> AgentState:
        if remote_task_id not in self._tasks:
            raise KeyError(remote_task_id)
        return AgentState.COMPLETED

    def result(self, remote_task_id: str) -> AgentResult:
        self.status(remote_task_id)
        return AgentResult(AgentState.COMPLETED, (f"mock://{remote_task_id}",))
