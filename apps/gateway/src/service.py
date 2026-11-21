import asyncio
import logging
import os
import time
from uuid import UUID

import httpx
from pydantic import ValidationError

from packages.channel_adapters.base import command
from packages.channel_adapters.discord import Discord
from packages.channel_adapters.telegram import Telegram
from packages.content_extractors import fetch_public_page
from packages.contracts import AuthContext, DeliveryRequest, EventEnvelope
from .store import Store

log = logging.getLogger("xmux.gateway")


def chunks(text, limit):
    pending = []
    size = 0
    for char in text:
        width = 2 if ord(char) > 65535 else 1
        if size + width > limit:
            yield "".join(pending)
            pending, size = [], 0
        pending.append(char)
        size += width
    if pending:
        yield "".join(pending)


class Gateway:
    def __init__(self, env=os.environ, transport=None):
        self.secret = env.get("GATEWAY_SERVICE_TOKEN", "")
        self.internal_token = env.get("ORCHESTRATOR_SERVICE_TOKEN", "")
        self.base = env.get("ORCHESTRATOR_URL", "").rstrip("/")
        self.tenant = UUID(env["XMUX_TENANT_ID"])
        if not all((self.secret, self.internal_token, self.base)):
            raise ValueError("Gateway and orchestrator configuration is required")
        self.store = Store(env.get("GATEWAY_DB", "./data/gateway.sqlite"))
        self.adapters = {
            "telegram": Telegram(env.get("TELEGRAM_WEBHOOK_SECRET", ""), env.get("TELEGRAM_BOT_TOKEN", ""),
                                 env.get("TELEGRAM_BOT_USERNAME", ""), tenant_id=self.tenant,
                                 enqueue=self.store.enqueue),
            "discord": Discord(env.get("DISCORD_PUBLIC_KEY", ""), env.get("DISCORD_BOT_TOKEN", ""),
                               env.get("DISCORD_APPLICATION_ID", ""), tenant_id=self.tenant,
                               enqueue=self.store.enqueue),
        }
        self.client = httpx.AsyncClient(timeout=8, trust_env=False, transport=transport)
        # httpx request logs include platform credentials embedded in URL paths.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    def intake(self, event: EventEnvelope, token=None):
        if command(event.text, "link") and event.auth_context.scope != "private":
            return
        key = f"{event.platform}:{event.platform_event_id}"
        interaction = (event.platform_event_id, token, time.time() + 840) if token else None
        return self.store.enqueue_event(key, event.model_dump(mode="json"), interaction)

    def enqueue_delivery(self, delivery):
        return self.adapters[delivery.channel].deliver(delivery)

    async def internal(self, path, **kwargs):
        return await self.client.post(self.base + path, headers={"Authorization": "Bearer " + self.internal_token},
                                      **kwargs)

    def notice(self, event, text):
        self.enqueue_delivery(DeliveryRequest(task_id=event.event_id, channel=event.platform,
                                              reply_target=event.reply_target, text=text))

    async def process_event(self, row):
        event = EventEnvelope.model_validate_json(row["body"])
        if command(event.text, "link"):
            words = event.text.split()
            if len(words) != 2 or len(words[1]) > 128:
                self.notice(event, "Use /link with the one-time code in a private chat.")
                return "failed", "invalid_link_code", 0, None
            response = await self.internal("/v1/channel-identities/link", json={
                "tenant_id": str(event.tenant_id), "platform": event.platform,
                "actor_external_id": event.actor_external_id, "code": words[1],
                "event_id": str(event.event_id),
            })
            if response.is_success:
                self.notice(event, "Your account is linked.")
                return "sent", None, 0, None
            if response.status_code in {400, 404, 409, 422}:
                self.notice(event, "This link code could not be redeemed. Request a new code and try again.")
                return "failed", "invalid_link_code", 0, None
            return self.internal_failure(response, row)
        lookup = await self.internal("/v1/channel-identities/resolve", json={
            "tenant_id": str(event.tenant_id), "platform": event.platform,
            "actor_external_id": event.actor_external_id,
        })
        if lookup.status_code == 404:
            self.notice(event, "Link your account in a private chat with /link before sending a request.")
            return "failed", "identity_unlinked", 0, None
        if not lookup.is_success:
            return self.internal_failure(lookup, row)
        identity = lookup.json()
        principal = UUID(identity["principal_id"])
        event.auth_context = AuthContext(linked=True, principal_id=principal, scope=event.auth_context.scope,
                                         permissions=identity["permissions"])
        # Fetch only after verifying identity. Captured material remains untrusted source data.
        event.sources = await asyncio.gather(*(fetch_public_page(str(url)) for url in event.urls))
        response = await self.internal("/v1/events", json=event.model_dump(mode="json"))
        if response.is_success:
            return "sent", None, 0, None
        return self.internal_failure(response, row)

    @staticmethod
    def internal_failure(response, row):
        status = response.status_code
        if (status == 429 or status >= 500) and row["attempts"] < 5:
            return "queued", "orchestrator_unavailable", retry_delay(response, 2 ** row["attempts"]), None
        return "failed", f"orchestrator_{status}", 0, None

    async def process_delivery(self, row):
        request = DeliveryRequest.model_validate_json(row["body"])
        adapter = self.adapters[request.channel]
        target = request.reply_target
        if target.interaction and request.channel != "discord":
            return "failed", "invalid_reply_target", 0, None
        reference = self.store.interaction(target.message_id) if target.interaction else None
        if target.interaction and not reference:
            return "failed", "interaction_expired", 0, None
        if not adapter.token and not reference:
            return "failed", "platform_unconfigured", 0, None
        message = "\n".join([request.text.strip(), *map(str, request.artifacts)]).strip()
        parts = list(chunks(message, 4000 if request.channel == "telegram" else 1950))
        for index in range(row["cursor"], len(parts)):
            method, url, headers, payload = adapter.build_message_request(target, parts[index], reference, index)
            try:
                response = await self.client.request(method, url, headers=headers, json=payload)
            except httpx.TransportError:
                return "unknown", "platform_send_uncertain", 0, None
            if response.status_code == 429 and row["attempts"] < 5:
                return "queued", "rate_limited", retry_delay(response, 2 ** row["attempts"]), None
            if response.status_code >= 500:
                return "unknown", "platform_send_uncertain", 0, None
            if not response.is_success:
                return "failed", f"platform_{response.status_code}", 0, None
            self.store.progress(row["id"], index + 1)
        return "sent", None, 0, None

    async def run_once(self):
        row = self.store.claim()
        if not row:
            return False
        try:
            if row["kind"] == "event":
                result = await self.process_event(row)
            else:
                result = await self.process_delivery(row)
        except httpx.TransportError:
            result = ("queued", "orchestrator_unavailable", 2 ** row["attempts"], None) if row["attempts"] < 5 else (
                "failed", "orchestrator_unavailable", 0, None)
        except (ValueError, TypeError, KeyError, ValidationError):
            result = ("failed" if row["kind"] == "event" else "unknown", "invalid_upstream_data", 0, None)
        self.store.finish(row["id"], *result)
        log.info("job=%s kind=%s state=%s error=%s", row["id"], row["kind"], result[0], result[1])
        return True

    async def run(self):
        while True:
            if not await self.run_once():
                await asyncio.sleep(0.15)


def retry_delay(response, default):
    value = response.headers.get("Retry-After")
    if value is None:
        try:
            body = response.json()
            value = body.get("retry_after") or body.get("parameters", {}).get("retry_after")
        except (ValueError, AttributeError):
            pass
    try:
        delay = float(value)
        if 0 <= delay < float("inf"):
            return max(0.2, delay)
    except (ValueError, TypeError):
        pass
    return default
