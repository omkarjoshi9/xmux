"""Authenticated WSGI discovery and dispatch interface for the control plane."""

import hmac
import json
from dataclasses import asdict
from typing import Callable

from .contracts import AgentTask, PersistedTaskReference
from .dispatch import DispatchContext, Dispatcher, UnknownDispatch, ApprovedCodingScope


def create_app(dispatcher: Dispatcher, service_token: str, *, references=None, approval_verifier=None) -> Callable:
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
            path = environ.get("PATH_INFO")
            method = environ.get("REQUEST_METHOD")
            if path == "/internal/agents/list" and method == "POST":
                configs = dispatcher.registry.discover_configs(principal)
                return respond(start_response, "200 OK", {"agents": [
                    {"agent_id": c.agent_id, "capabilities": sorted(c.operations)}
                    for c in configs
                ]})
            if path == "/internal/agents/status" or path == "/internal/agents/results":
                if method != "POST":
                    return respond(start_response, "405 Method Not Allowed", {"error":"method_not_allowed"})
                if references is None:
                    return respond(start_response, "503 Service Unavailable", {"error":"reference_store_unavailable"})
                key = data.get("task_id")
                ref = references.get(key)
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
            approval = data.get("approval")
            scope = approval_verifier(principal, tenant, approval, task) if approval_verifier and approval else None
            if task.intent == "code_change" and scope is None:
                return respond(start_response, "403 Forbidden", {"error":"approval_required"})
            receipt = dispatcher.submit(task, DispatchContext(principal, tenant, scope), data.get("requested_agent"))
            ref = PersistedTaskReference(receipt.agent_id, receipt.remote_task_id, tenant, principal)
            if references is not None:
                references[task.task_id] = ref
            result = asdict(receipt)
            result["state"] = receipt.state.value
            return respond(start_response, "200 OK", result)
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
