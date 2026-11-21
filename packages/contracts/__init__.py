from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


Platform = Literal["telegram", "discord"]


class ReplyTarget(Contract):
    channel_id: str = Field(pattern=r"^-?\d+$")
    message_id: str = Field(pattern=r"^\d+$")
    thread_id: str | None = Field(default=None, pattern=r"^\d+$")
    interaction: bool = False


class AuthContext(Contract):
    linked: bool
    principal_id: UUID | None = None
    scope: Literal["private", "group"]
    permissions: list[str] = Field(default_factory=list)


class Attachment(Contract):
    id: str
    kind: str
    name: str | None = None
    url: HttpUrl | None = None


class Source(Contract):
    url: HttpUrl
    status: Literal["available", "content_unavailable"]
    canonical_url: HttpUrl | None = None
    title: str = ""
    description: str = ""
    excerpt: str = ""


class EventEnvelope(Contract):
    event_id: UUID
    platform: Platform
    platform_event_id: str
    event_type: Literal["direct_message", "mention", "capture"]
    occurred_at: datetime
    actor_external_id: str
    tenant_id: UUID
    text: str
    urls: list[HttpUrl]
    attachments: list[Attachment]
    reply_target: ReplyTarget
    auth_context: AuthContext
    guild_id: str | None = None
    forwarded: bool = False
    replied_to_message_id: str | None = None
    sources: list[Source] = Field(default_factory=list)


class DeliveryRequest(Contract):
    task_id: UUID
    channel: Platform
    reply_target: ReplyTarget
    text: str = Field(max_length=20000)
    artifacts: list[HttpUrl] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def has_content(self):
        if not self.text.strip() and not self.artifacts:
            raise ValueError("delivery requires text or an artifact")
        return self


class DeliveryReceipt(Contract):
    delivery_id: UUID
    state: Literal["queued", "running", "sent", "failed", "unknown"]
    error_code: str | None = None
