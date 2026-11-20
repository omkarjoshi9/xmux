"""T3 Code bridge for a configured, approved project and worktree."""

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from .contracts import AgentResult, AgentState, AgentTask


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
        if task.payload.get("allowed_tools"):
            raise ValueError("t3_tool_scope_unsupported")
        response = self._client.call("t3_create_thread", {
            "project": self._project,
            "harness": self._harness,
            "model": self._model,
            "title": f"XMUX {task.task_id}",
            "prompt": task.objective,
            "newWorktree": {"baseBranch": base_branch, "branch": branch, "runSetupScript": False},
            "runtimeMode": "approval-required",
            "idempotencyKey": task.idempotency_key,
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

    def status(self, thread_id: str) -> AgentState:
        result = self.read_thread(thread_id)
        turn = result.get("turn")
        if not isinstance(turn, Mapping):
            raise LookupError("t3_turn_unavailable")
        states = {"running": AgentState.RUNNING, "completed": AgentState.COMPLETED,
                  "interrupted": AgentState.INTERRUPTED, "error": AgentState.FAILED}
        state = turn.get("state")
        if state not in states:
            raise LookupError("t3_turn_state_unknown")
        return states[state]

    def result(self, thread_id: str) -> AgentResult | None:
        result = self.read_thread(thread_id)
        turn = result.get("turn")
        if not isinstance(turn, Mapping) or turn.get("state") != "completed":
            return None
        url = result.get("url")
        if not isinstance(url, str) or not url:
            raise LookupError("t3_thread_reference_unavailable")
        return AgentResult(AgentState.COMPLETED, (url,))

    def cancel(self, thread_id: str) -> Mapping[str, object]:
        if not thread_id:
            raise ValueError("thread_id_required")
        return self._client.call("t3_cancel_turn", {"threadId": thread_id})
