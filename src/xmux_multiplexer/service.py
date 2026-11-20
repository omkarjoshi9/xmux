"""Compose the multiplexer with trusted control-plane and provider integrations."""

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .contracts import AgentAdapter
from .dispatch import Dispatcher
from .dispatch_ledger import DispatchLedger
from .http import create_app
from .registry import AgentConfig, AgentRegistry
from .schema_validation import SharedSchemaValidator


def create_service(*, service_token: str, database: str, schema_directory: str,
                   agents: Iterable[AgentConfig], adapters: Mapping[str, AgentAdapter],
                   authorize_principal: Callable[..., bool], verify_delegation: Callable[..., dict],
                   orchestrator_service_id: str, approval_verifier: Callable[..., Any],
                   openbot=None, grokbot=None, resolve_reference=None):
    """Build WSGI service from server-side configuration and authenticated hooks.

    The supplied hooks must authenticate principal ownership, verify delegation
    signatures and action fingerprints, and verify approvals with the control plane.
    Adapter credentials and endpoints are provisioned by the host, never request data.
    """
    if not callable(authorize_principal) or not callable(verify_delegation) or not callable(approval_verifier):
        raise ValueError("control_plane_verifiers_required")
    if not orchestrator_service_id:
        raise ValueError("orchestrator_service_id_required")
    registry = AgentRegistry(agents)
    for adapter_id, adapter in adapters.items():
        if adapter.id != adapter_id:
            raise ValueError("adapter_id_mismatch")
    dispatcher = Dispatcher(registry, adapters)
    return create_app(
        dispatcher, service_token, dispatch_ledger=DispatchLedger(database),
        schema_validator=SharedSchemaValidator(schema_directory),
        authorize_principal=authorize_principal, verify_delegation=verify_delegation,
        orchestrator_service_id=orchestrator_service_id, approval_verifier=approval_verifier,
        openbot=openbot, grokbot=grokbot, resolve_reference=resolve_reference,
    )
