"""T3 Code bridge for a configured, approved project and worktree."""

from dataclasses import dataclass
import hashlib
import json
from typing import Callable, Mapping, Protocol

from .contracts import AgentCapabilities, AgentResult, AgentState, AgentTask, RemoteTaskReceipt


class T3Client(Protocol):
    def call(self, tool: str, arguments: Mapping[str, object]) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class T3Submission:
    """The raw bridge receipt; the control plane resolves its thread reference."""
    response: Mapping[str, object]


class T3Bridge:
    def __init__(self, client: T3Client, project: str, harness: str, model: str,
                 authorize: Callable[[AgentTask, str, str], bool]):
        if not all((project, harness, model)):
            raise ValueError("t3_configuration_incomplete")
        self._client, self._project, self._harness, self._model = client, project, harness, model
        self._authorize = authorize

    def submit(self, task: AgentTask, *, base_branch: str, branch: str) -> T3Submission:
        if not base_branch or not branch:
            raise ValueError("branch_required")
        if not self._authorize(task, self._project, branch):
            raise PermissionError("coding_scope_not_approved")
        if task.required_capabilities - {"coding"}:
            raise ValueError("unsupported_capability")
        if "allowed_tools" in task.payload:
            raise ValueError("t3_tool_scope_unsupported")
        response = self._client.call("t3_create_thread", {
            "project": self._project,
            "harness": self._harness,
            "model": self._model,
            "title": f"XMUX {task.task_id}",
            "prompt": task.objective,
            "newWorktree": {"baseBranch": base_branch, "branch": branch, "runSetupScript": False},
            "runtimeMode": "approval-required",
            "idempotencyKey": hashlib.sha256(json.dumps(
                [task.tenant_id, task.principal_id, task.task_id, task.idempotency_key],
                separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8")).hexdigest(),
            "wait": False,
        })
        return T3Submission(response)

    def read_thread(self, thread_id: str) -> Mapping[str, object]:
        if not thread_id:
            raise ValueError("thread_id_required")
        result = self._client.call("t3_get_thread", {"threadId": thread_id})
        if result.get("threadId") != thread_id:
            raise ValueError("unexpected_t3_thread")
        return result

    def status(self, thread_id: str, turn_id: str) -> AgentState:
        result = self.read_thread(thread_id)
        turn = result.get("turn")
        if not isinstance(turn, Mapping) or turn.get("turnId") != turn_id:
            raise LookupError("t3_turn_unavailable_or_superseded")
        states = {"running": AgentState.RUNNING, "completed": AgentState.COMPLETED,
                  "interrupted": AgentState.INTERRUPTED, "error": AgentState.FAILED}
        state = turn.get("state")
        if state not in states:
            raise LookupError("t3_turn_state_unknown")
        return states[state]

    def result(self, thread_id: str, turn_id: str) -> AgentResult | None:
        result = self.read_thread(thread_id)
        turn = result.get("turn")
        if not isinstance(turn, Mapping) or turn.get("turnId") != turn_id:
            raise LookupError("t3_turn_unavailable_or_superseded")
        if turn.get("state") != "completed":
            return None
        url = result.get("url")
        if not isinstance(url, str) or not url:
            raise LookupError("t3_thread_reference_unavailable")
        return AgentResult(AgentState.COMPLETED, (url,))

    def cancel(self, thread_id: str) -> Mapping[str, object]:
        if not thread_id:
            raise ValueError("thread_id_required")
        return self._client.call("t3_cancel_turn", {"threadId": thread_id})


class T3Agent:
    """Bridge T3 thread and turn references into the agent adapter interface."""

    id = "t3"

    def __init__(self, bridge: T3Bridge):
        self._bridge = bridge

    def capabilities(self):
        return AgentCapabilities(frozenset({"coding"}), supports_status=True, supports_cancel=False)

    def health(self):
        return "degraded"

    def submit(self, task: AgentTask) -> RemoteTaskReceipt:
        base_branch = task.payload.get("base_branch")
        branch = task.payload.get("branch")
        if not isinstance(base_branch, str) or not isinstance(branch, str):
            raise ValueError("t3_branches_required")
        submission = self._bridge.submit(task, base_branch=base_branch, branch=branch)
        response = submission.response
        turn = response.get("turn")
        thread_id = response.get("threadId")
        if not isinstance(thread_id, str) or not isinstance(turn, Mapping):
            raise LookupError("t3_receipt_incomplete")
        turn_id = turn.get("turnId")
        if not isinstance(turn_id, str) or not turn_id:
            raise LookupError("t3_receipt_incomplete")
        remote_ref = json.dumps({"thread_id": thread_id, "turn_id": turn_id}, separators=(",", ":"))
        if len(remote_ref) > 512:
            raise ValueError("t3_remote_reference_too_long")
        return RemoteTaskReceipt(self.id, remote_ref, AgentState.RUNNING)

    @staticmethod
    def _reference(remote_ref: str) -> tuple[str, str]:
        value = json.loads(remote_ref)
        if not isinstance(value, dict) or set(value) != {"thread_id", "turn_id"}:
            raise ValueError("invalid_t3_remote_reference")
        thread, turn = value["thread_id"], value["turn_id"]
        if not isinstance(thread, str) or not thread or not isinstance(turn, str) or not turn:
            raise ValueError("invalid_t3_remote_reference")
        return thread, turn

    def status(self, remote_ref: str):
        return self._bridge.status(*self._reference(remote_ref))

    def result(self, remote_ref: str):
        return self._bridge.result(*self._reference(remote_ref))
