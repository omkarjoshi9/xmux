"""Conversion between shared wire contracts and internal adapter values."""

from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID

from .contracts import AgentCapabilities, AgentTask, RemoteTaskReceipt


def task_from_wire(
    request: Mapping[str, Any],
    *,
    task_id: str,
    principal_id: str,
    tenant_id: str,
    required_capabilities: frozenset[str] = frozenset(),
) -> AgentTask:
    """Convert a validated v1 TaskRequest using identity supplied by the caller.

    The service boundary is responsible for validating against the shared JSON
    Schema and authenticating the separately supplied identity and task ID.
    """
    if request.get("schema_version") != "1":
        raise ValueError("unsupported_schema_version")
    return AgentTask(
        task_id=task_id,
        idempotency_key=request["idempotency_key"],
        principal_id=principal_id,
        tenant_id=tenant_id,
        intent=request["intent"],
        objective=request["objective"],
        required_capabilities=required_capabilities,
        payload={
            key: request[key]
            for key in ("source_event_id", "requested_agent", "context_refs", "reply_target_ref")
            if key in request
        },
    )


def receipt_to_wire(
    receipt: RemoteTaskReceipt,
    *,
    task_id: str,
    correlation_id: str,
    accepted_at: datetime,
) -> dict[str, str]:
    """Create a v1 RemoteTaskRef after provider acceptance."""
    UUID(task_id)
    UUID(correlation_id)
    if accepted_at.tzinfo is None or accepted_at.utcoffset() is None:
        raise ValueError("accepted_at_requires_timezone")
    timestamp = accepted_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": "1",
        "task_id": task_id,
        "agent_id": receipt.agent_id,
        "remote_task_id": receipt.remote_task_id,
        "correlation_id": correlation_id,
        "accepted_at": timestamp,
    }


def capabilities_to_wire(agent_id: str, capabilities: AgentCapabilities) -> dict[str, object]:
    """Expose only explicitly declared support in Dev 3's v1 schema."""
    retention = capabilities.dispatch_idempotency_retention_seconds
    if retention is not None and not 1 <= retention <= 31536000:
        raise ValueError("invalid_idempotency_retention")
    idempotency = ({"supported": False} if retention is None else
                   {"supported": True, "scope": "principal_agent_operation",
                    "retention_seconds": retention})
    return {
        "schema_version": "1", "agent_id": agent_id,
        "operations": sorted(capabilities.operations),
        "supports_status": capabilities.supports_status,
        "supports_cancel": capabilities.supports_cancel,
        "supports_schedule": capabilities.supports_schedule,
        "supports_memory_lookup": capabilities.supports_memory_lookup,
        "supports_callbacks": capabilities.supports_callbacks,
        "supports_correlation_lookup": capabilities.supports_correlation_lookup,
        "dispatch_idempotency": idempotency,
    }
