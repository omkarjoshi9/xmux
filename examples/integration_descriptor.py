"""Describe an authorized CLI coding mux; no command is executed here."""
from xmux_multiplexer.integrations import (
    IntegrationCategory, IntegrationDescriptor, Operation, OperationSupport,
    SecretReference, SupportState, TransportKind,
)

descriptor = IntegrationDescriptor(
    integration_id="team-coding-mux",
    display_name="Team coding mux",
    category=IntegrationCategory.CODING_MUX,
    transports=frozenset({TransportKind.CLI}),
    operations=(
        OperationSupport(Operation.SUBMIT_TASK, SupportState.SUPPORTED),
        OperationSupport(Operation.GET_STATUS, SupportState.SUPPORTED),
        OperationSupport(Operation.GET_RESULT, SupportState.REQUIRES_CONFIGURATION,
                         "result_adapter_not_configured"),
        OperationSupport(Operation.CANCEL_TASK, SupportState.UNSUPPORTED,
                         "mux_does_not_expose_cancel"),
    ),
    secret_refs=(SecretReference("secret-store/team-coding-mux"),),
)
