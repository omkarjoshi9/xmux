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
                   openbot=None, grokbot=None, resolve_reference=None, instinct_recipients=None):
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
        instinct_recipients=instinct_recipients,
    )


def create_introspected_service(*, service_token: str, database: str, schema_directory: str,
                                provider_definitions: list[Mapping[str, Any]], mcp_clients: Mapping[str, Any],
                                introspection_endpoint: str, introspection_credential: Callable[[], str],
                                resolve_secret: Callable[[str], str], orchestrator_service_id: str):
    """Compose configured providers with authenticated Dev 3 introspection."""
    from .introspection import Dev3Introspection
    from .provider_factory import build_providers

    verifier = Dev3Introspection(introspection_endpoint, introspection_credential)
    providers = build_providers(
        provider_definitions, database=database, resolve_secret=resolve_secret,
        authorize_task=verifier.authorize_provider_task,
        authorize_identity_change=verifier.authorize_identity_change, mcp_clients=mcp_clients,
    )
    return create_service(
        service_token=service_token, database=database, schema_directory=schema_directory,
        agents=providers.agents, adapters=providers.adapters,
        authorize_principal=verifier.authorize_principal, verify_delegation=verifier.verify_delegation,
        approval_verifier=verifier.verify_approval, orchestrator_service_id=orchestrator_service_id,
        openbot=providers.openbot, grokbot=providers.grokbot, resolve_reference=providers.resolve_reference,
        instinct_recipients=providers.instinct_recipients,
    )
