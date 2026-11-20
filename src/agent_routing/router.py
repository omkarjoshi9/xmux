"""Pure, deterministic agent selection; dispatch and persistence belong to callers."""
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Tuple


class Capability(str, Enum):
    RESEARCH = "research"
    CODING = "coding"
    SCHEDULING = "scheduling"


@dataclass(frozen=True)
class Agent:
    id: str
    capabilities: frozenset[Capability]
    authorized: bool = True
    healthy: bool = True
    preference: int = 0  # Higher values are preferred.


@dataclass(frozen=True)
class RoutingRequest:
    capability: Capability
    target_id: Optional[str] = None
    additional_capabilities: frozenset[Capability] = frozenset()


@dataclass(frozen=True)
class RouteDecision:
    agent: Agent
    explicit: bool


class RoutingError(ValueError):
    """No permitted route exists, or an explicit target is invalid."""


def select_agent(request: RoutingRequest, agents: Iterable[Agent]) -> RouteDecision:
    """Select authorized, capable, healthy agent; explicit invalid targets never fall back."""
    candidates = tuple(agents)
    required = request.additional_capabilities | {request.capability}
    if request.target_id is not None:
        matches = [a for a in candidates if a.id == request.target_id]
        if len(matches) != 1:
            raise RoutingError(f"explicit target {request.target_id!r} is missing or ambiguous")
        agent = matches[0]
        if not agent.authorized or not required.issubset(agent.capabilities) or not agent.healthy:
            raise RoutingError(f"explicit target {request.target_id!r} is not eligible")
        return RouteDecision(agent, True)
    eligible = [a for a in candidates if a.authorized and a.healthy and required.issubset(a.capabilities)]
    if not eligible:
        raise RoutingError(f"no eligible agent for {request.capability.value}")
    return RouteDecision(sorted(eligible, key=lambda a: (-a.preference, a.id))[0], False)
