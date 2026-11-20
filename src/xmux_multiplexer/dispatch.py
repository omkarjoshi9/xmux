"""Dispatch to explicitly registered adapters using trusted authorization context.

The authenticated service boundary must construct DispatchContext after checking
the calling service and validating the user's approval with the control plane.
"""

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import Lock
from time import monotonic
from typing import Mapping

from .contracts import AgentAdapter, AgentTask, RemoteTaskReceipt
from .registry import AgentRegistry


@dataclass(frozen=True)
class ApprovedCodingScope:
    repository: str
    branch: str
    allowed_tools: frozenset[str]


@dataclass(frozen=True)
class DispatchContext:
    principal_id: str
    tenant_id: str
    coding_scope: ApprovedCodingScope | None = None


class UnknownDispatch(RuntimeError):
    """The provider may have accepted the request; reconcile before retrying."""

    def __init__(self, task_id: str, code: str = "submission_outcome_unknown"):
        self.task_id, self.code = task_id, code
        super().__init__(code)


class CircuitOpen(RuntimeError):
    def __init__(self, agent_id: str):
        self.agent_id, self.code = agent_id, "agent_circuit_open"
        super().__init__(self.code)


class Dispatcher:
    def __init__(self, registry: AgentRegistry, adapters: Mapping[str, AgentAdapter], *,
                 timeout_seconds: float = 5.0, failure_threshold: int = 3,
                 recovery_seconds: float = 30.0):
        if timeout_seconds <= 0 or failure_threshold < 1 or recovery_seconds <= 0:
            raise ValueError("invalid_dispatch_limits")
        self.registry = registry
        self.adapters = dict(adapters)
        self.timeout_seconds, self.failure_threshold, self.recovery_seconds = timeout_seconds, failure_threshold, recovery_seconds
        self._lock = Lock()
        self._failures: dict[str, int] = {}
        self._opened: dict[str, float] = {}

    def _call(self, agent_id: str, operation):
        with self._lock:
            opened = self._opened.get(agent_id)
            if opened is not None:
                if monotonic() - opened < self.recovery_seconds:
                    raise CircuitOpen(agent_id)
                del self._opened[agent_id]  # one half-open probe
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(operation)
        try:
            result = future.result(timeout=self.timeout_seconds)
        except (TimeoutError, FutureTimeout):
            future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            with self._lock:
                count = self._failures.get(agent_id, 0) + 1
                self._failures[agent_id] = count
                if count >= self.failure_threshold:
                    self._opened[agent_id] = monotonic()
            raise
        except Exception:
            pool.shutdown(wait=False, cancel_futures=True)
            with self._lock:
                count = self._failures.get(agent_id, 0) + 1
                self._failures[agent_id] = count
                if count >= self.failure_threshold:
                    self._opened[agent_id] = monotonic()
            raise
        else:
            pool.shutdown(wait=False)
            with self._lock:
                self._failures.pop(agent_id, None)
                self._opened.pop(agent_id, None)
            return result

    def submit(self, task: AgentTask, context: DispatchContext,
               requested_agent: str | None = None) -> RemoteTaskReceipt:
        if task.principal_id != context.principal_id or task.tenant_id != context.tenant_id:
            raise PermissionError("identity_mismatch")
        if task.intent == "code_change" or "coding" in task.required_capabilities:
            scope = context.coding_scope
            tools = task.payload.get("allowed_tools")
            if (scope is None or task.payload.get("repository") != scope.repository
                    or task.payload.get("branch") != scope.branch
                    or not isinstance(tools, (list, tuple))
                    or any(not isinstance(tool, str) for tool in tools)
                    or not set(tools).issubset(scope.allowed_tools)):
                raise PermissionError("coding_scope_mismatch")
        intent_capabilities = {
            "research": "research",
            "summarize": "summarize",
            "code_change": "coding",
            "schedule": "schedule",
        }
        required = task.required_capabilities
        if task.intent in intent_capabilities:
            required = required | {intent_capabilities[task.intent]}
        candidates = self.registry.discover_configs(context.principal_id, required)
        if requested_agent is not None:
            candidates = tuple(c for c in candidates if c.agent_id == requested_agent)
        available = []
        for config in candidates:
            adapter = self.adapters.get(config.agent_id)
            if (adapter is not None and adapter.id == config.agent_id
                    and required.issubset(adapter.capabilities().operations)
                    and self._call(adapter.id, adapter.health) == "healthy"):
                available.append(adapter)
        if not available:
            raise LookupError("no_capable_agent")
        adapter = min(available, key=lambda agent: agent.id)
        try:
            try:
                return self._call(adapter.id, lambda: adapter.submit(task))
            except (TimeoutError, FutureTimeout) as exc:
                raise UnknownDispatch(task.task_id) from exc
            except Exception as exc:
                raise UnknownDispatch(task.task_id, "submission_failed") from exc
        except CircuitOpen as exc:
            raise LookupError("no_capable_agent") from exc
