from datetime import UTC, datetime
from hmac import compare_digest

from packages.contracts import Attachment, EventEnvelope, ReplyTarget
from ..base import ChannelAdapter, command


class Telegram(ChannelAdapter):
    name = "telegram"

    def __init__(self, secret, token, username, *, tenant_id, enqueue):
        super().__init__(tenant_id, enqueue)
        self.secret, self.token, self.username = secret, token, username.lower().lstrip("@")

    def verify(self, headers, body):
        return bool(self.secret) and compare_digest(
            headers.get("x-telegram-bot-api-secret-token", "").encode(), self.secret.encode())

    def parse(self, update) -> list[EventEnvelope]:
        msg = update.get("message")
        if not msg or not msg.get("from") or msg["from"].get("is_bot") or msg.get("sender_chat"):
            return []
        chat = msg["chat"]
        if chat["type"] not in {"private", "group", "supergroup"}:
            return []
        private = chat["type"] == "private"
        text = msg.get("text", msg.get("caption", ""))
        entities = msg.get("entities", msg.get("caption_entities", []))
        encoded = text.encode("utf-16-le")
        urls = []
        mentioned = False
        for entity in entities:
            start = entity["offset"] * 2
            value = encoded[start:start + entity["length"] * 2].decode("utf-16-le", errors="replace")
            if entity["type"] == "mention" and value.lower() == "@" + self.username:
                mentioned = True
            if entity["type"] in {"url", "text_link"}:
                urls.append(entity.get("url", value))
        first = (text.split(maxsplit=1) or [""])[0]
        if first.startswith("/") and "@" in first and first.split("@", 1)[1].lower() != self.username:
            return []
        capture = command(text, "capture")
        if not (private or mentioned or capture):
            return []
        attachments = []
        for kind in ("photo", "document", "video", "audio", "voice", "animation", "sticker", "video_note"):
            item = msg.get(kind)
            if isinstance(item, list):
                item = item[-1] if item else None
            if item:
                attachments.append(Attachment(id=item["file_id"], kind=kind, name=item.get("file_name")))
        reply = msg.get("reply_to_message", {}).get("message_id")
        thread = msg.get("message_thread_id")
        return [self.event(
            str(update["update_id"]), str(msg["from"]["id"]), datetime.fromtimestamp(msg["date"], UTC),
            "capture" if capture else "direct_message" if private else "mention", private, text,
            ReplyTarget(channel_id=str(chat["id"]), message_id=str(msg["message_id"]),
                        thread_id=str(thread) if thread is not None else None),
            forwarded=bool(msg.get("forward_origin") or msg.get("forward_date")),
            reply_to=str(reply) if reply is not None else None, urls=urls, attachments=attachments,
        )]

    def build_message_request(self, target, text, reference, index):
        payload = {"chat_id": target.channel_id, "text": text,
                   "reply_parameters": {"message_id": target.message_id},
                   "link_preview_options": {"is_disabled": True}}
        if target.thread_id:
            payload["message_thread_id"] = target.thread_id
        return "POST", f"https://api.telegram.org/bot{self.token}/sendMessage", {}, payload
