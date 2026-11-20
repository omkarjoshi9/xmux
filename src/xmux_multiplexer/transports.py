"""Configured CLI and injected MCP coding mux transports.

These adapters map the XMUX task contract onto explicitly configured operations.
They do not claim support for any particular provider or execute repository code.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
from typing import Callable, Mapping, Protocol, Sequence

from .contracts import AgentCapabilities, AgentResult, AgentState, AgentTask, RemoteTaskReceipt


def _task_payload(task: AgentTask) -> dict[str, object]:
    # Only contract fields are sent; arbitrary payload cannot override identity/scope.
    return {
        "task_id": task.task_id,
        "idempotency_key": task.idempotency_key,
        "principal_id": task.principal_id,
        "tenant_id": task.tenant_id,
        "intent": task.intent,
        "objective": task.objective,
        "required_capabilities": sorted(task.required_capabilities),
        "payload": dict(task.payload),
    }


def _receipt(agent_id: str, value: Mapping[str, object]) -> RemoteTaskReceipt:
    remote_id = value.get("remote_task_id")
    state = value.get("state")
    if not isinstance(remote_id, str) or not remote_id:
        raise ValueError("missing_remote_task_id")
    try:
        normalized = AgentState(state)
    except (ValueError, TypeError) as exc:
        raise ValueError("unknown_provider_state") from exc
    return RemoteTaskReceipt(agent_id, remote_id, normalized)


def _result(value: Mapping[str, object]) -> AgentResult:
    try:
        state = AgentState(value.get("state"))
    except (ValueError, TypeError) as exc:
        raise ValueError("unknown_provider_state") from exc
    refs = value.get("result_refs", [])
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        raise ValueError("invalid_result_refs")
    return AgentResult(state, tuple(refs))


@dataclass(frozen=True)
class CliMuxConfig:
    agent_id: str
    command: tuple[str, ...]
    operations: Mapping[str, tuple[str, ...]]
    timeout_seconds: float = 30.0

    def __post_init__(self):
        if not self.agent_id or not self.command or any(not isinstance(x, str) or not x for x in self.command):
            raise ValueError("invalid_cli_command")
        if self.timeout_seconds <= 0:
            raise ValueError("invalid_timeout")
        if set(self.operations) - {"submit", "status", "result"}:
            raise ValueError("unsupported_operation")
        if "submit" not in self.operations or "status" not in self.operations:
            raise ValueError("missing_required_operation")
        for args in self.operations.values():
            if not args or any(not isinstance(x, str) for x in args):
                raise ValueError("invalid_operation_arguments")


class CliMuxAgent:
    """Runs only an operator-configured executable and fixed argument mappings."""

    def __init__(self, config: CliMuxConfig, authorize: Callable[[AgentTask], bool], runner=None):
        self.config, self.id, self.authorize = config, config.agent_id, authorize
        self.runner = runner or subprocess.run

    def capabilities(self):
        return AgentCapabilities(frozenset({"coding"}), supports_status=True,
                                 supports_cancel=False)

    def health(self):
        return "degraded"

    def _call(self, operation: str, payload: Mapping[str, object]):
        try:
            args = self.config.operations[operation]
        except KeyError as exc:
            raise NotImplementedError("operation_not_configured") from exc
        proc = self.runner([*self.config.command, *args], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=self.config.timeout_seconds,
                           shell=False, check=False)
        if proc.returncode:
            raise RuntimeError("cli_transport_failed")
        try:
            output = json.loads(proc.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_provider_response") from exc
        if not isinstance(output, dict):
            raise ValueError("invalid_provider_response")
        return output

    def submit(self, task: AgentTask):
        if not task.required_capabilities.issubset({"coding"}):
            raise ValueError("unsupported_capability")
        if not self.authorize(task):
            raise PermissionError("coding_scope_not_approved")
        return _receipt(self.id, self._call("submit", _task_payload(task)))

    def status(self, remote_task_id: str):
        return AgentState(self._call("status", {"remote_task_id": remote_task_id}).get("state"))

    def result(self, remote_task_id: str):
        if "result" not in self.config.operations:
            raise NotImplementedError("operation_not_configured")
        return _result(self._call("result", {"remote_task_id": remote_task_id}))


class McpClient(Protocol):
    def call_tool(self, name: str, arguments: Mapping[str, object]) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class McpMuxConfig:
    agent_id: str
    tools: Mapping[str, str]

    def __post_init__(self):
        if not self.agent_id or set(self.tools) - {"submit", "status", "result"}:
            raise ValueError("invalid_mcp_operation_map")
        if "submit" not in self.tools or "status" not in self.tools or any(not x for x in self.tools.values()):
            raise ValueError("missing_required_operation")


class McpMuxAgent:
    """Uses an injected, already-authorized MCP client; never establishes credentials."""

    def __init__(self, config: McpMuxConfig, client: McpClient,
                 authorize: Callable[[AgentTask], bool]):
        self.config, self.id, self.client, self.authorize = config, config.agent_id, client, authorize

    def capabilities(self):
        return AgentCapabilities(frozenset({"coding"}), supports_status=True)

    def health(self):
        return "degraded"

    def _call(self, operation: str, payload: Mapping[str, object]):
        try:
            value = self.client.call_tool(self.config.tools[operation], payload)
        except KeyError as exc:
            raise NotImplementedError("operation_not_configured") from exc
        if not isinstance(value, Mapping):
            raise ValueError("invalid_provider_response")
        return value

    def submit(self, task: AgentTask):
        if not task.required_capabilities.issubset({"coding"}):
            raise ValueError("unsupported_capability")
        if not self.authorize(task):
            raise PermissionError("coding_scope_not_approved")
        return _receipt(self.id, self._call("submit", _task_payload(task)))

    def status(self, remote_task_id: str):
        try:
            return AgentState(self._call("status", {"remote_task_id": remote_task_id}).get("state"))
        except (ValueError, TypeError) as exc:
            raise ValueError("unknown_provider_state") from exc

    def result(self, remote_task_id: str):
        if "result" not in self.config.tools:
            raise NotImplementedError("operation_not_configured")
        return _result(self._call("result", {"remote_task_id": remote_task_id}))
