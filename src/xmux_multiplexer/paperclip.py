"""Paperclip issue adapter using the documented issue API.

The injected client handles authenticated requests and transport timeouts.
A create timeout is ambiguous and must be reconciled by the control plane.
"""
from typing import Mapping, Protocol
from urllib.parse import quote

from .contracts import AgentCapabilities, AgentResult, AgentState, AgentTask, RemoteTaskReceipt


class PaperclipClient(Protocol):
    def post(self, path: str, body: Mapping[str, str]) -> Mapping[str, object]: ...
    def get(self, path: str) -> Mapping[str, object]: ...
    def issue_reference(self, issue_id: str) -> str: ...


class PaperclipAdapter:
    def __init__(self, client: PaperclipClient, company_id: str, assignee_id: str):
        if not company_id or not assignee_id:
            raise ValueError("company_and_assignee_required")
        self.id = "paperclip"
        self._client = client
        self._company = quote(company_id, safe="")
        self._assignee = assignee_id

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(frozenset({"delegate"}), supports_status=True)

    def health(self) -> str:
        return "degraded"  # No probe is configured.

    def submit(self, task: AgentTask) -> RemoteTaskReceipt:
        if not task.required_capabilities.issubset({"delegate"}):
            raise ValueError("unsupported_capability")
        issue = self._client.post(
            f"/api/companies/{self._company}/issues",
            {"title": task.objective[:255], "description": task.objective,
             "status": "todo", "assigneeAgentId": self._assignee},
        )
        issue_id = issue.get("id")
        if not isinstance(issue_id, str) or not issue_id:
            raise ValueError("missing_remote_issue_id")
        return RemoteTaskReceipt(self.id, issue_id, self._state(issue))

    def status(self, remote_task_id: str) -> AgentState:
        issue = self._client.get(f"/api/issues/{quote(remote_task_id, safe='')}")
        return self._state(issue)

    def result(self, remote_task_id: str) -> AgentResult | None:
        issue = self._client.get(f"/api/issues/{quote(remote_task_id, safe='')}")
        state = self._state(issue)
        if state != AgentState.COMPLETED:
            return None
        # The issue is the owning provider's durable record. It can contain
        # comments, documents, and attachments accessible through Paperclip.
        return AgentResult(state, (self._client.issue_reference(remote_task_id),))

    @staticmethod
    def _state(issue: Mapping[str, object]) -> AgentState:
        states = {"backlog": AgentState.RUNNING, "todo": AgentState.RUNNING,
                  "in_progress": AgentState.RUNNING, "in_review": AgentState.RUNNING,
                  "blocked": AgentState.RUNNING, "done": AgentState.COMPLETED,
                  "cancelled": AgentState.FAILED}
        try:
            return states[issue["status"]]
        except (KeyError, TypeError) as exc:
            raise ValueError("unknown_paperclip_status") from exc
