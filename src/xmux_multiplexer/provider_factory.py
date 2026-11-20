"""Build integrations from trusted server-side configuration.

Repository URLs are metadata only. This module never downloads or executes them.
The host supplies verified authorization, secret resolution and MCP clients.
"""

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .agentmail import AgentMailClient
from .agentmail_instinct import AgentMailInstinct, InstinctAgent
from .generic_mux import HttpMuxAgent, HttpMuxConfig
from .grokbot import GrokBotWebhook
from .instinct_identity import InstinctRecipientStore
from .openbot import OpenBotWebhook
from .paperclip import PaperclipAdapter
from .paperclip_http import PaperclipHttpClient
from .registry import AgentConfig
from .t3 import T3Agent, T3Bridge
from .transports import CliMuxAgent, CliMuxConfig, McpMuxAgent, McpMuxConfig


@dataclass(frozen=True)
class Providers:
    agents: tuple[AgentConfig, ...]
    adapters: Mapping[str, Any]
    openbot: Any = None
    grokbot: Any = None
    resolve_reference: Any = None
    instinct_recipients: Any = None


def build_providers(definitions: list[Mapping[str, Any]], *, database: str,
                    resolve_secret: Callable[[str], str],
                    authorize_task: Callable[..., bool],
                    authorize_identity_change: Callable[..., bool],
                    mcp_clients: Mapping[str, Any]) -> Providers:
    """Construct configured integrations without granting user-supplied code execution.

    Definitions and authorization callbacks must come from authenticated,
    administrator-controlled service configuration.
    """
    if not all(callable(value) for value in (resolve_secret, authorize_task, authorize_identity_change)):
        raise ValueError("trusted_provider_callbacks_required")
    agents, adapters = [], {}
    openbot = grokbot = resolver = recipient_store = None
    seen = set()
    for definition in definitions:
        agent_id, kind = definition["id"], definition["kind"]
        if not isinstance(agent_id, str) or not agent_id or agent_id in seen:
            raise ValueError("duplicate_or_invalid_provider_id")
        seen.add(agent_id)
        tenants, principals = frozenset(definition["tenants"]), frozenset(definition["principals"])
        if not tenants or not principals:
            raise ValueError("provider_owner_scope_required")
        settings = definition["settings"]
        operations = frozenset(definition["operations"])
        credential_ref = settings.get("credential_ref")
        auth = lambda task, current=agent_id: authorize_task(task, current)

        if kind == "http_mux":
            state_map = settings["state_map"]
            from .contracts import AgentState
            parsed = {key: AgentState(value) for key, value in state_map.items()}
            config = HttpMuxConfig(agent_id=agent_id, endpoint=settings["endpoint"],
                                   submit_path=settings["submit_path"], status_path=settings["status_path"],
                                   remote_id_field=settings["remote_id_field"], state_field=settings["state_field"],
                                   state_map=parsed, result_refs_field=settings["result_refs_field"],
                                   credential_ref=credential_ref, repository_url=settings.get("repository_url"),
                                   timeout_seconds=settings.get("timeout_seconds", 10))
            adapters[agent_id] = HttpMuxAgent(config, resolve_secret, auth)
            endpoint, transport = settings["endpoint"], "http_api"
        elif kind == "cli_mux":
            command = tuple(settings["command"])
            mapping = {key: tuple(value) for key, value in settings["operations"].items()}
            adapters[agent_id] = CliMuxAgent(CliMuxConfig(agent_id, command, mapping,
                                                         settings.get("timeout_seconds", 30)), auth)
            endpoint, transport = command[0], "cli"
        elif kind == "mcp_mux":
            server = settings["server"]
            adapters[agent_id] = McpMuxAgent(McpMuxConfig(agent_id, settings["operations"]), mcp_clients[server], auth)
            endpoint, transport = server, "mcp"
        elif kind == "paperclip":
            if agent_id != "paperclip":
                raise ValueError("paperclip_provider_id_must_match_adapter")
            client = PaperclipHttpClient(settings["endpoint"], lambda ref=credential_ref: resolve_secret(ref))
            adapters[agent_id] = PaperclipAdapter(client, settings["company_id"], settings["assignee_id"])
            endpoint, transport = settings["endpoint"], "http_api"
        elif kind == "t3":
            if agent_id != "t3":
                raise ValueError("t3_provider_id_must_match_adapter")
            server = settings["server"]
            bridge = T3Bridge(mcp_clients[server], settings["project"], settings["harness"], settings["model"],
                              lambda task, project, branch: authorize_task(task, "t3", project, branch))
            adapters[agent_id] = T3Agent(bridge)
            endpoint, transport = server, "mcp"
        elif kind == "instinct_email":
            if agent_id != "instinct-email" or resolver is not None:
                raise ValueError("invalid_instinct_provider")
            recipient_store = InstinctRecipientStore(database, authorize_identity_change)
            mail = AgentMailClient(resolve_secret(credential_ref))
            instinct = AgentMailInstinct(mail, database, settings["inbox_id"], recipient_store)
            adapters[agent_id], resolver = InstinctAgent(instinct), instinct.resolve_reference
            endpoint, transport = settings["inbox_id"], "email"
        elif kind == "openbot":
            if agent_id != "openbot" or openbot is not None:
                raise ValueError("invalid_openbot_provider")
            openbot = OpenBotWebhook(settings["endpoint"], lambda ref=credential_ref: resolve_secret(ref))
            endpoint, transport = settings["endpoint"], "webhook"
        elif kind == "grokbot":
            if agent_id != "grokbot" or grokbot is not None:
                raise ValueError("invalid_grokbot_provider")
            grokbot = GrokBotWebhook(settings["endpoint"], credential_ref, resolve_secret,
                                     frozenset({"routine:trigger"}))
            endpoint, transport = settings["endpoint"], "webhook"
        else:
            raise ValueError("unsupported_provider_kind")
        agents.append(AgentConfig(agent_id, endpoint, operations, principals, credential_ref, tenants, transport))
    return Providers(tuple(agents), adapters, openbot, grokbot, resolver, recipient_store)
