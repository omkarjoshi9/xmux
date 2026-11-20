"""Authenticated WSGI discovery and dispatch interface for the control plane."""

import hmac
import json
from datetime import datetime, timezone
from dataclasses import asdict
from typing import Callable

from .contracts import AgentTask, PersistedTaskReference
from .dispatch import DispatchContext, Dispatcher, UnknownDispatch, ApprovedCodingScope
from .dispatch_ledger import DispatchConflict, DispatchInProgress


def create_app(dispatcher: Dispatcher, service_token: str, *, references=None, approval_verifier=None,
               openbot=None, dispatch_ledger=None, authorize_principal=None, verify_delegation=None,
               schema_validator=None, orchestrator_service_id=None, audience="multiplexer") -> Callable:
    if not service_token:
        raise ValueError("service_token_required")

    def app(environ, start_response):
        supplied = environ.get("HTTP_AUTHORIZATION", "")
        expected = "Bearer " + service_token
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            return respond(start_response, "401 Unauthorized", {"error": "unauthorized"})
        try:
            length = int(environ.get("CONTENT_LENGTH") or "0")
            if length < 1 or length > 65536:
                return respond(start_response, "400 Bad Request", {"error": "invalid_request_size"})
            data = json.loads(environ["wsgi.input"].read(length))
            if not isinstance(data, dict):
                raise ValueError("invalid_request")
            principal = data["principal_id"]
            tenant = data["tenant_id"]
            if not isinstance(principal, str) or not principal.strip() or not isinstance(tenant, str) or not tenant.strip():
                raise ValueError("invalid_identity")
            if authorize_principal is None or not authorize_principal(principal, tenant, data):
                return respond(start_response, "403 Forbidden", {"error": "forbidden"})
            path = environ.get("PATH_INFO")
            method = environ.get("REQUEST_METHOD")
            if path == "/internal/agents/list" and method == "POST":
                configs = dispatcher.registry.discover_configs(principal, tenant_id=tenant)
                return respond(start_response, "200 OK", {"agents": [
                    {"agent_id": c.agent_id, "capabilities": sorted(c.operations)}
                    for c in configs
                ]})
            if path == "/internal/agents/status" or path == "/internal/agents/results":
                if method != "POST":
                    return respond(start_response, "405 Method Not Allowed", {"error":"method_not_allowed"})
                if references is None and dispatch_ledger is None:
                    return respond(start_response, "503 Service Unavailable", {"error":"reference_store_unavailable"})
                key = data.get("task_id")
                ref = references.get(key) if references is not None else None
                if ref is None and dispatch_ledger is not None:
                    stored = dispatch_ledger.get_receipt(tenant, principal, key)
                    if stored and stored.get("state") == "delivery_accepted":
                        return respond(start_response, "422 Unprocessable Entity", {"error": "unsupported_capability"})
                    if stored:
                        ref = PersistedTaskReference(stored["agent_id"], stored["remote_task_id"], tenant, principal)
                if ref is None:
                    return respond(start_response, "404 Not Found", {"error":"unknown_task"})
                context = DispatchContext(principal, tenant)
                if path.endswith("/status"):
                    state = dispatcher.status(ref, context)
                    return respond(start_response, "200 OK", {"task_id":key,"state":state.value})
                result = dispatcher.result(ref, context)
                return respond(start_response, "200 OK", {"task_id":key,"result":asdict(result) if result else None})
            if path != "/internal/agents/dispatch" or method != "POST":
                return respond(start_response, "404 Not Found", {"error": "not_found"})
            task_data = data["task"]
            if not isinstance(task_data, dict):
                raise ValueError("invalid_task")
            for key in ("task_id", "idempotency_key", "intent", "objective"):
                if not isinstance(task_data.get(key), str) or not task_data[key].strip():
                    raise ValueError("invalid_task")
            caps = task_data.get("required_capabilities", [])
            payload = task_data.get("payload", {})
            if not isinstance(caps, list) or any(not isinstance(c, str) for c in caps) or not isinstance(payload, dict):
                raise ValueError("invalid_task")
            task = AgentTask(task_data["task_id"], task_data["idempotency_key"], principal, tenant,
                             task_data["intent"], task_data["objective"], frozenset(caps), payload)
            # Coding writes require an independently verified approval integration.
            requested_agent = data.get("requested_agent")
            if schema_validator is None or verify_delegation is None or not orchestrator_service_id:
                return respond(start_response, "503 Service Unavailable", {"error": "delegation_verifier_unavailable"})
            # Verification must authenticate the grant and bind its action fingerprint
            # to this exact task before returning claims. Raw request claims are not trusted.
            grant = verify_delegation(principal, tenant, task, requested_agent, data.get("delegation"))
            if not isinstance(grant, dict):
                return respond(start_response, "403 Forbidden", {"error": "invalid_delegation"})
            schema_validator.validate("DelegationContext", grant)
            now = datetime.now(timezone.utc)
            issued = datetime.fromisoformat(grant["issued_at"].replace("Z", "+00:00"))
            expires = datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
            if (grant["principal_id"] != principal or grant["tenant_id"] != tenant
                    or grant["task_id"] != task.task_id or grant["scope"] != "tasks:dispatch"
                    or grant["actor_service_id"] != orchestrator_service_id or grant["audience"] != audience
                    or issued.tzinfo is None or expires.tzinfo is None or issued > now or expires <= now
                    or (requested_agent is not None and grant["agent_id"] != requested_agent)):
                return respond(start_response, "403 Forbidden", {"error": "invalid_delegation"})
            # The verified grant binds a single agent, including automatic selections.
            requested_agent = grant["agent_id"]
            if dispatch_ledger is None:
                return respond(start_response, "503 Service Unavailable", {"error": "dispatch_ledger_unavailable"})
            approval = data.get("approval")
            scope = approval_verifier(principal, tenant, approval, task) if approval_verifier and approval else None
            if task.intent == "code_change" or "coding" in task.required_capabilities:
                tools = payload.get("allowed_tools")
                if (scope is None or payload.get("repository") != scope.repository
                        or payload.get("branch") != scope.branch or not isinstance(tools, list)
                        or any(not isinstance(tool, str) for tool in tools)
                        or not set(tools).issubset(scope.allowed_tools)):
                    return respond(start_response, "403 Forbidden", {"error": "coding_scope_mismatch"})
            if requested_agent == "openbot":
                if openbot is None:
                    return respond(start_response, "503 Service Unavailable", {"error": "openbot_not_configured"})
                eligible = dispatcher.registry.discover_configs(principal, frozenset({"coding"}), tenant)
                if not any(config.agent_id == "openbot" for config in eligible):
                    return respond(start_response, "403 Forbidden", {"error": "forbidden"})
                tools = payload.get("allowed_tools")
                if (scope is None or payload.get("repository") != scope.repository
                        or payload.get("branch") != scope.branch or not isinstance(tools, list)
                        or any(not isinstance(tool, str) for tool in tools)
                        or not set(tools).issubset(scope.allowed_tools)):
                    return respond(start_response, "403 Forbidden", {"error": "coding_scope_mismatch"})
                # The injected control-plane store must atomically reserve and
                # persist the task fingerprint before an external side effect.
                reservation = dispatch_ledger.reserve(task, "openbot")
                if reservation.state == "accepted":
                    return respond(start_response, "200 OK", json.loads(reservation.remote_ref))
                try:
                    delivery = openbot.submit(
                        tenant_id=tenant, task_id=task.task_id, idempotency_key=task.idempotency_key,
                        objective=task.objective, repository=scope.repository, branch=scope.branch,
                        allowed_tools=tuple(tools), authorize=lambda: True,
                    )
                except Exception as exc:
                    raise UnknownDispatch(task.task_id) from exc
                result = {"delivery_id": delivery.delivery_id, "state": "delivery_accepted",
                          "execution_state": "unknown", "duplicate": delivery.duplicate}
                dispatch_ledger.accepted(task, "openbot", json.dumps(result, separators=(",", ":")))
                return respond(start_response, "202 Accepted", result)
            reservation_agent = requested_agent or "automatic"
            reservation = dispatch_ledger.reserve(task, reservation_agent)
            if reservation.state == "accepted":
                return respond(start_response, "200 OK", json.loads(reservation.remote_ref))
            receipt = dispatcher.submit(task, DispatchContext(principal, tenant, scope), requested_agent)
            ref = PersistedTaskReference(receipt.agent_id, receipt.remote_task_id, tenant, principal)
            if references is not None:
                references[task.task_id] = ref
            result = asdict(receipt)
            result["state"] = receipt.state.value
            dispatch_ledger.accepted(task, reservation_agent, json.dumps(result, separators=(",", ":")))
            return respond(start_response, "200 OK", result)
        except DispatchConflict:
            return respond(start_response, "409 Conflict", {"error": "idempotency_conflict"})
        except DispatchInProgress:
            return respond(start_response, "409 Conflict", {"error": "remote_outcome_unknown"})
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return respond(start_response, "400 Bad Request", {"error": "invalid_request"})
        except PermissionError:
            return respond(start_response, "403 Forbidden", {"error": "forbidden"})
        except LookupError:
            return respond(start_response, "422 Unprocessable Entity", {"error": "no_capable_agent"})
        except UnknownDispatch:
            return respond(start_response, "503 Service Unavailable", {"error": "unknown_dispatch"})

    return app


def respond(start_response, status, payload):
    body = json.dumps(payload).encode("utf-8")
    start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
    return [body]
