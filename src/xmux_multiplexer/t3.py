"""T3 Code bridge for a configured, approved project and worktree."""

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from .contracts import AgentTask


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
