import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from packages.content_extractors import extract_urls
from packages.contracts import AuthContext, DeliveryReceipt, DeliveryRequest, EventEnvelope


def command(text, name):
    words = text.split(maxsplit=1)
    return bool(words) and words[0].split("@", 1)[0].lower() == "/" + name


class ChannelAdapter:
    def __init__(self, tenant_id, enqueue):
        self.tenant_id = tenant_id
        self.enqueue = enqueue

    def event(self, identifier, actor, timestamp, kind, private, text, target, *,
              guild=None, forwarded=False, reply_to=None, urls=(), attachments=()) -> EventEnvelope:
        key = f"{self.name}:{identifier}"
        return EventEnvelope(
            event_id=uuid5(NAMESPACE_URL, f"xmux:{self.tenant_id}:{key}"),
            platform=self.name, platform_event_id=identifier, event_type=kind,
            occurred_at=timestamp, actor_external_id=actor, tenant_id=self.tenant_id,
            text=text, urls=extract_urls(text, urls), reply_target=target,
            attachments=list(attachments), guild_id=guild, forwarded=forwarded,
            replied_to_message_id=reply_to,
            auth_context=AuthContext(linked=False, scope="private" if private else "group"),
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryReceipt:
        """Queue a reply and return its current delivery receipt."""
        body = request.model_dump(mode="json")
        key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        row, _ = self.enqueue("delivery", key, body)
        return delivery_receipt(row)


def delivery_receipt(row) -> DeliveryReceipt:
    return DeliveryReceipt(delivery_id=row["id"], state=row["state"], error_code=row["error"])
