"""Authenticated client for the Dev 3 control-plane introspection boundary.

The JSON envelope is a proposed integration contract. Dev 3 must implement the
same operations before this verifier can authorize production traffic.
"""

import json
from dataclasses import asdict
from typing import Callable, Mapping
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .contracts import AgentTask
from .dispatch import ApprovedCodingScope


class IntrospectionUnavailable(RuntimeError):
    """The control plane could not verify an authorization decision."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def task_document(task: AgentTask) -> dict:
    value = asdict(task)
    value["required_capabilities"] = sorted(task.required_capabilities)
    return value


class Dev3Introspection:
    def __init__(self, endpoint: str, credential: Callable[[], str], *, timeout: float = 5, opener=None):
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
                or parsed.query or parsed.fragment or timeout <= 0):
            raise ValueError("invalid_introspection_endpoint")
        if not callable(credential):
            raise ValueError("introspection_credential_resolver_required")
        self._endpoint, self._credential, self._timeout = endpoint, credential, timeout
        self._opener = opener or build_opener(NoRedirect())

    def _check(self, operation: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        credential = self._credential()
        if not isinstance(credential, str) or not credential:
            raise IntrospectionUnavailable("introspection_credential_unavailable")
        body = json.dumps({"operation": operation, **payload}, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(body) > 1048576:
            raise IntrospectionUnavailable("introspection_request_too_large")
        request = Request(self._endpoint, data=body, method="POST",
                          headers={"Authorization": "Bearer " + credential, "Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read(1048577)
            if len(raw) > 1048576:
                raise ValueError("response_too_large")
            result = json.loads(raw)
        except Exception as exc:
            raise IntrospectionUnavailable("introspection_unavailable") from exc
        if not isinstance(result, dict) or not isinstance(result.get("active"), bool):
            raise IntrospectionUnavailable("introspection_invalid_response")
        return result

    def authorize_principal(self, principal: str, tenant: str, data: Mapping[str, object],
                            path: str, method: str) -> bool:
        task = data.get("task")
        task_id = task.get("task_id") if isinstance(task, Mapping) else None
        result = self._check("authorize_principal", {
            "principal_id": principal, "tenant_id": tenant, "path": path, "method": method,
            "task_id": data.get("task_id") or task_id,
            "evidence": data.get("principal_evidence"),
        })
        return result["active"] is True

    def verify_delegation(self, principal: str, tenant: str, task: AgentTask,
                          agent_id: str | None, grant: object) -> dict | None:
        result = self._check("verify_delegation", {
            "principal_id": principal, "tenant_id": tenant, "agent_id": agent_id,
            "task": task_document(task), "grant": grant,
        })
        claims = result.get("claims")
        return claims if result["active"] is True and isinstance(claims, dict) else None

    def verify_approval(self, principal: str, tenant: str, approval: object,
                        task: AgentTask) -> ApprovedCodingScope | None:
        result = self._check("verify_approval", {
            "principal_id": principal, "tenant_id": tenant, "approval": approval,
            "task": task_document(task),
        })
        scope = result.get("coding_scope")
        if result["active"] is not True or not isinstance(scope, dict):
            return None
        repository, branch, tools = scope.get("repository"), scope.get("branch"), scope.get("allowed_tools")
        if (not isinstance(repository, str) or not repository or not isinstance(branch, str) or not branch
                or not isinstance(tools, list) or any(not isinstance(tool, str) or not tool for tool in tools)):
            raise IntrospectionUnavailable("introspection_invalid_scope")
        return ApprovedCodingScope(repository, branch, frozenset(tools))

    def authorize_provider_task(self, task: AgentTask, agent_id: str, *scope: str) -> bool:
        result = self._check("authorize_provider_task", {
            "agent_id": agent_id, "task": task_document(task), "provider_scope": list(scope),
        })
        return result["active"] is True

    def authorize_identity_change(self, tenant: str, principal: str, recipient: str, proof: str) -> bool:
        result = self._check("authorize_identity_change", {
            "tenant_id": tenant, "principal_id": principal, "recipient": recipient, "proof": proof,
        })
        return result["active"] is True
