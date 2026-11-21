import time
from datetime import UTC, datetime
from urllib.parse import quote

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from packages.contracts import Attachment, EventEnvelope, ReplyTarget
from ..base import ChannelAdapter


class Discord(ChannelAdapter):
    name = "discord"

    def __init__(self, public_key, token, application_id, *, tenant_id, enqueue):
        super().__init__(tenant_id, enqueue)
        self.public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)) if public_key else None
        self.token, self.application_id = token, application_id

    def verify(self, headers, body):
        if self.public_key is None:
            return False
        try:
            timestamp = headers["x-signature-timestamp"]
            if abs(time.time() - int(timestamp)) > 300:
                return False
            self.public_key.verify(bytes.fromhex(headers["x-signature-ed25519"]), timestamp.encode() + body)
            return True
        except (ValueError, KeyError, InvalidSignature):
            return False

    def parse(self, payload) -> list[EventEnvelope]:
        if payload.get("type") != 2 or payload.get("data", {}).get("name") not in {"capture", "link"}:
            return []
        data = payload["data"]
        actor = payload.get("member", {}).get("user") or payload["user"]
        private = not payload.get("guild_id")
        if data["name"] == "link" and not private:
            return []
        text = " ".join(str(option["value"]) for option in data.get("options", []))
        identifier = str(payload["id"])
        return [self.event(
            identifier, str(actor["id"]), self.timestamp(identifier),
            "capture", private, "/" + data["name"] + " " + text,
            ReplyTarget(channel_id=str(payload["channel_id"]), message_id=identifier, interaction=True),
            guild=payload.get("guild_id"),
        )]

    @staticmethod
    def timestamp(identifier):
        return datetime.fromtimestamp(((int(identifier) >> 22) + 1420070400000) / 1000, UTC)

    def parse_message(self, msg) -> list[EventEnvelope]:
        if msg.author.bot or msg.webhook_id:
            return []
        private = msg.guild is None
        if not private and not any(str(user.id) == self.application_id for user in msg.mentions):
            return []
        return [self.event(
            str(msg.id), str(msg.author.id), msg.created_at.astimezone(UTC),
            "direct_message" if private else "mention", private, msg.content,
            ReplyTarget(channel_id=str(msg.channel.id), message_id=str(msg.id)),
            guild=str(msg.guild.id) if msg.guild else None,
            reply_to=str(msg.reference.message_id) if msg.reference and msg.reference.message_id else None,
            attachments=[Attachment(id=str(item.id), kind="file", name=item.filename, url=item.url)
                         for item in msg.attachments],
        )]

    def build_message_request(self, target, text, reference, index):
        payload = {"content": text, "allowed_mentions": {"parse": [], "replied_user": False}}
        if target.interaction and reference:
            if index > 0:
                payload["flags"] = 64
                url = f"https://discord.com/api/v10/webhooks/{self.application_id}/{quote(reference, safe='')}?wait=true"
                return "POST", url, {}, payload
            return ("PATCH", f"https://discord.com/api/v10/webhooks/{self.application_id}/{reference}/messages/@original",
                    {}, payload)
        if not target.interaction:
            payload["message_reference"] = {"message_id": target.message_id, "fail_if_not_exists": True}
        return ("POST", f"https://discord.com/api/v10/channels/{target.channel_id}/messages",
                {"Authorization": "Bot " + self.token}, payload)
