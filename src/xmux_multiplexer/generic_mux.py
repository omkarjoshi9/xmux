"""Configured HTTP coding mux integration.

The control plane must authenticate users and approve task scopes before
calling this adapter. Configuration and endpoint mappings are operator-owned.
"""

from dataclasses import dataclass
import json
from typing import Callable, Mapping
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .contracts import AgentCapabilities, AgentResult, AgentState, AgentTask, RemoteTaskReceipt


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class HttpMuxConfig:
    agent_id: str
    endpoint: str
    submit_path: str
    status_path: str
    remote_id_field: str
    state_field: str
    state_map: Mapping[str, AgentState]
    result_refs_field: str
    credential_ref: str
    repository_url: str | None = None
    timeout_seconds: float = 10.0

    def __post_init__(self):
        parsed = urlsplit(self.endpoint)
        if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
                or parsed.query or parsed.fragment or self.timeout_seconds <= 0):
            raise ValueError("invalid_endpoint")
        for path in (self.submit_path, self.status_path):
            if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path:
                raise ValueError("invalid_operation_path")
        if self.status_path.count("{remote_id}") != 1:
            raise ValueError("invalid_status_path")
        if not self.credential_ref.startswith(("env:", "vault:")):
            raise ValueError("invalid_credential_reference")


class HttpMuxAgent:
    def __init__(self, config: HttpMuxConfig, resolve_secret: Callable[[str], str],
                 authorize: Callable[[AgentTask], bool], opener=None):
        self.config = config
        self.id = config.agent_id
        self.resolve_secret = resolve_secret
        self.authorize = authorize
        self.opener = opener or build_opener(NoRedirect())

    def capabilities(self):
        return AgentCapabilities(frozenset({"coding"}), supports_status=True)

    def health(self):
        # Connectivity is checked during provider operations.
        return "degraded"

    def _request(self, method, path, body=None):
        token = self.resolve_secret(self.config.credential_ref)
        request = Request(self.config.endpoint.rstrip("/") + path, method=method,
                          data=None if body is None else json.dumps(body).encode("utf-8"),
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with self.opener.open(request, timeout=self.config.timeout_seconds) as response:
            raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ValueError("provider_response_too_large")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("invalid_provider_response")
        return value

    def submit(self, task):
        if not task.required_capabilities.issubset({"coding"}):
            raise ValueError("unsupported_capability")
        if not self.authorize(task):
            raise PermissionError("coding_scope_not_approved")
        payload = {"task_id": task.task_id, "objective": task.objective,
                   "idempotency_key": task.idempotency_key, **dict(task.payload)}
        # Authenticated routing metadata must win over arbitrary task payload.
        payload.update(task_id=task.task_id, objective=task.objective, idempotency_key=task.idempotency_key)
        data = self._request("POST", self.config.submit_path, payload)
        remote_id = data.get(self.config.remote_id_field)
        if not isinstance(remote_id, str) or not remote_id:
            raise ValueError("missing_remote_task_id")
        return RemoteTaskReceipt(self.id, remote_id, self._state(data))

    def _state(self, data):
        try:
            return self.config.state_map[data[self.config.state_field]]
        except (KeyError, TypeError) as exc:
            raise ValueError("unknown_provider_state") from exc

    def _fetch(self, remote_task_id):
        path = self.config.status_path.replace("{remote_id}", quote(remote_task_id, safe=""))
        return self._request("GET", path)

    def status(self, remote_task_id):
        return self._state(self._fetch(remote_task_id))

    def result(self, remote_task_id):
        data = self._fetch(remote_task_id)
        refs = data.get(self.config.result_refs_field, [])
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            raise ValueError("invalid_result_refs")
        return AgentResult(self._state(data), tuple(refs))
