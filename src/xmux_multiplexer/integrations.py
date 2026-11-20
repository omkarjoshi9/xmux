"""Provider-neutral descriptions and protocols for XMUX integrations.

These contracts describe authorized, configured connections. They never fetch
repository URLs or contain credential values; implementations own execution.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class IntegrationCategory(StrEnum):
    CODING_MUX = "coding_mux"
    SOCIAL = "social"
    MESSAGING = "messaging"
    EMAIL = "email"


class TransportKind(StrEnum):
    HTTP_API = "http_api"
    CLI = "cli"
    MCP = "mcp"
    EMAIL = "email"
    WEBHOOK = "webhook"


class Operation(StrEnum):
    SUBMIT_TASK = "submit_task"
    GET_STATUS = "get_status"
    GET_RESULT = "get_result"
    CANCEL_TASK = "cancel_task"
    CAPTURE_INBOUND = "capture_inbound"
    SEND_MESSAGE = "send_message"
    SEND_EMAIL = "send_email"
    RECEIVE_WEBHOOK = "receive_webhook"


class SupportState(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    REQUIRES_CONFIGURATION = "requires_configuration"


@dataclass(frozen=True)
class OperationSupport:
    operation: Operation
    state: SupportState
    reason: str | None = None


@dataclass(frozen=True)
class IdentityReference:
    """Opaque reference to an XMUX-linked principal/account, never an identity token."""

    reference: str


@dataclass(frozen=True)
class SecretReference:
    """Opaque secret-manager key; secret material must not be stored here."""

    reference: str


@dataclass(frozen=True)
class IntegrationDescriptor:
    integration_id: str
    display_name: str
    category: IntegrationCategory
    transports: frozenset[TransportKind]
    operations: tuple[OperationSupport, ...]
    identity: IdentityReference | None = None
    secret_refs: tuple[SecretReference, ...] = ()
    configuration: dict[str, str] = field(default_factory=dict)

    def support_for(self, operation: Operation) -> OperationSupport:
        return next((item for item in self.operations if item.operation == operation),
                    OperationSupport(operation, SupportState.UNSUPPORTED,
                                     "operation_not_declared"))


class Integration(Protocol):
    """Authorized integration implementations expose only declared operations."""

    @property
    def descriptor(self) -> IntegrationDescriptor: ...


class CodingMuxIntegration(Integration, Protocol):
    def submit_task(self, task: object) -> object: ...
    def get_status(self, task_ref: str) -> object: ...
    def get_result(self, task_ref: str) -> object: ...


class ChannelIntegration(Integration, Protocol):
    def capture_inbound(self, event: object) -> object: ...
    def send_message(self, recipient: IdentityReference, content: str) -> object: ...


def supports(descriptor: IntegrationDescriptor, operation: Operation) -> bool:
    """Return declared support without guessing mappings from a provider name."""
    return descriptor.support_for(operation).state is SupportState.SUPPORTED
