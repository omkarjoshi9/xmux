"""Validated agent configuration and principal-scoped discovery."""

from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlsplit

from .contracts import AgentCapabilities


@dataclass(frozen=True)
class AgentConfig:
    agent_id: str
    endpoint: str
    operations: frozenset[str]
    principals: frozenset[str]
    credential_ref: str | None = None
    tenants: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.agent_id or any(c.isspace() for c in self.agent_id):
            raise ValueError("invalid_agent_id")
        parsed = urlsplit(self.endpoint)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("invalid_endpoint")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("endpoint_must_not_contain_credentials_or_query")
        if not self.operations or any(not x.strip() for x in self.operations):
            raise ValueError("invalid_operations")
        if not self.principals or any(not x.strip() for x in self.principals):
            raise ValueError("invalid_principals")
        if self.credential_ref is not None:
            scheme, separator, name = self.credential_ref.partition(":")
            if scheme not in {"env", "vault"} or not separator or not name.strip():
                raise ValueError("credential_ref_must_be_a_secret_reference")


class AgentRegistry:
    def __init__(self, configs: Iterable[AgentConfig] = ()) -> None:
        self._configs: dict[str, AgentConfig] = {}
        for config in configs:
            self.register(config)

    def register(self, config: AgentConfig) -> None:
        if config.agent_id in self._configs:
            raise ValueError("duplicate_agent_id")
        self._configs[config.agent_id] = config

    def discover(self, principal_id: str, required: frozenset[str] = frozenset()) -> tuple[AgentCapabilities, ...]:
        return tuple(AgentCapabilities(config.operations) for config in self._configs.values()
                     if principal_id in config.principals and required.issubset(config.operations))

    def discover_configs(self, principal_id: str, required: frozenset[str] = frozenset(),
                         tenant_id: str | None = None) -> tuple[AgentConfig, ...]:
        return tuple(c for c in self._configs.values()
                     if principal_id in c.principals and required.issubset(c.operations)
                     and (tenant_id is None or tenant_id in c.tenants))
