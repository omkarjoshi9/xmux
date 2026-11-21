import json
import tempfile
import time
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apps.gateway.src.main import MAX_BODY, create_app
from apps.gateway.src.service import chunks
from apps.gateway.src.store import Store
from packages.content_extractors import check_address, extract_urls, fetch_public_page


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.key = Ed25519PrivateKey.generate()
        self.principal = str(uuid4())
        self.task_id = str(uuid4())
        self.events = []
        self.sends = []
        self.lookups = []
        self.linked = True
        self.outcomes = []
        self.env = {
            "XMUX_TENANT_ID": str(uuid4()),
            "GATEWAY_DB": temp.name + "/jobs.sqlite",
            "GATEWAY_SERVICE_TOKEN": "inside",
            "ORCHESTRATOR_SERVICE_TOKEN": "outside",
            "ORCHESTRATOR_URL": "https://orchestrator.test",
            "TELEGRAM_WEBHOOK_SECRET": "secret",
            "TELEGRAM_BOT_TOKEN": "telegram-token",
            "TELEGRAM_BOT_USERNAME": "xmux_bot",
            "DISCORD_PUBLIC_KEY": self.key.public_key().public_bytes_raw().hex(),
            "DISCORD_APPLICATION_ID": "555",
            "DISCORD_BOT_TOKEN": "discord-token",
        }
        self.app = create_app(self.env, httpx.MockTransport(self.upstream), start_worker=False)
        await self.enterAsyncContext(self.app.router.lifespan_context(self.app))
        self.gateway = self.app.state.gateway
        self.http = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://app.test"))
        self.telegram_headers = {"x-telegram-bot-api-secret-token": "secret"}
        self.service_headers = {"Authorization": "Bearer inside"}

    def upstream(self, request):
        body = json.loads(request.content)
        if request.url.host == "orchestrator.test":
            self.assertEqual(request.headers["authorization"], "Bearer outside")
            if request.url.path.endswith("/resolve"):
                self.lookups.append(body)
                return httpx.Response(200, json={"principal_id": self.principal, "permissions": ["capture"]}) if (
                    self.linked) else httpx.Response(404)
            if request.url.path.endswith("/link"):
                self.assertEqual(body["code"], "one-time")
                self.linked = True
                return httpx.Response(200)
            self.events.append(body)
            return httpx.Response(202, json={"task_id": self.task_id})
        self.sends.append((request.method, request.url, body))
        outcome = self.outcomes.pop(0) if self.outcomes else 200
        if outcome == "timeout":
            raise httpx.ReadTimeout("uncertain", request=request)
        return httpx.Response(outcome, headers={"Retry-After": "0.2"} if outcome == 429 else {})

    def telegram(self, text="/capture", chat_type="private"):
        return {"update_id": 50, "message": {"date": 1700000000, "message_id": 1, "from": {"id": 2},
                                              "chat": {"id": 3, "type": chat_type}, "text": text}}

    async def send_telegram(self, payload=None):
        return await self.http.post("/webhooks/telegram", json=payload or self.telegram(),
                                    headers=self.telegram_headers)

    async def send_discord(self, payload, timestamp=None):
        body = json.dumps(payload).encode()
        timestamp = timestamp or str(int(time.time()))
        signature = self.key.sign(timestamp.encode() + body).hex()
        return await self.http.post("/webhooks/discord", content=body, headers={
            "x-signature-timestamp": timestamp, "x-signature-ed25519": signature,
        })

    async def submit_delivery(self, channel="telegram", target=None, text=None):
        return await self.http.post("/v1/deliveries", headers=self.service_headers, json={
            "task_id": self.task_id, "channel": channel, "reply_target": target or {"channel_id": "3", "message_id": "1"},
            "text": text or f"Result for {self.task_id}",
        })

    async def drain(self):
        while await self.gateway.run_once():
            pass

    async def test_telegram_round_trip(self):
        for _ in range(2):
            self.assertEqual((await self.send_telegram()).status_code, 200)
        await self.drain()
        self.assertEqual(len(self.events), 1)
        event = self.events[0]
        self.assertEqual(event["auth_context"]["principal_id"], self.principal)
        self.assertEqual(event["platform_event_id"], "50")
        delivery = await self.submit_delivery(target=event["reply_target"])
        duplicate = await self.submit_delivery(target=event["reply_target"])
        self.assertEqual(delivery.json()["delivery_id"], duplicate.json()["delivery_id"])
        await self.drain()
        self.assertEqual(len(self.sends), 1)
        self.assertEqual(self.sends[0][2]["reply_parameters"]["message_id"], "1")
        self.assertIn(self.task_id, self.sends[0][2]["text"])
        receipt_url = "/v1/deliveries/" + delivery.json()["delivery_id"]
        self.assertEqual((await self.http.get(receipt_url, headers=self.service_headers)).json()["state"], "sent")

    async def test_discord_round_trip(self):
        payload = {"application_id": "555", "id": "1134567890123456789", "type": 2, "channel_id": "7",
                   "token": "interaction_token", "member": {"user": {"id": "8"}}, "guild_id": "9",
                   "data": {"name": "capture", "options": [{"name": "text", "value": "A note"}]}}
        for _ in range(2):
            response = await self.send_discord(payload)
            self.assertEqual(response.json(), {"type": 5, "data": {"flags": 64}})
        self.assertEqual(self.events, [])
        await self.drain()
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0]["guild_id"], "9")
        self.assertEqual(self.events[0]["text"], "/capture A note")
        await self.submit_delivery("discord", self.events[0]["reply_target"])
        await self.drain()
        method, url, body = self.sends[0]
        self.assertEqual(method, "PATCH")
        self.assertTrue(url.path.endswith("interaction_token/messages/@original"))
        self.assertIn(self.task_id, body["content"])
        self.assertEqual(body["allowed_mentions"]["parse"], [])

    async def test_discord_gateway_direct_message_and_mention(self):
        adapter = self.gateway.adapters["discord"]
        channel = SimpleNamespace(id=7)
        author = SimpleNamespace(id=8, bot=False)
        for identifier, guild, mentions in [(11, None, []), (12, SimpleNamespace(id=9), []),
                                             (13, SimpleNamespace(id=9), [SimpleNamespace(id=555)])]:
            message = SimpleNamespace(id=identifier, author=author, channel=channel, guild=guild,
                                      mentions=mentions, created_at=datetime.now(UTC), webhook_id=None,
                                      content="A note", attachments=[], reference=None)
            for event in adapter.parse_message(message):
                self.gateway.intake(event)
        await self.drain()
        self.assertEqual([event["event_type"] for event in self.events], ["direct_message", "mention"])
        await self.submit_delivery("discord", self.events[1]["reply_target"])
        await self.drain()
        self.assertEqual(self.sends[0][2]["message_reference"]["message_id"], "13")

    async def test_per_actor_ingress_rate_limit(self):
        with patch("apps.gateway.src.store.time.time", return_value=1800):
            for index in range(21):
                payload = self.telegram()
                payload["update_id"] += index
                self.assertEqual((await self.send_telegram(payload)).status_code, 200)
        await self.drain()
        self.assertEqual(len(self.events), 20)

    async def test_webhook_credentials(self):
        self.assertEqual((await self.http.post("/webhooks/telegram", json=self.telegram())).status_code, 401)
        self.assertEqual((await self.http.post("/webhooks/discord", json={})).status_code, 401)
        old = str(int(time.time()) - 301)
        self.assertEqual((await self.send_discord({"type": 1, "application_id": "555"}, old)).status_code, 401)
        self.assertEqual((await self.send_discord({"type": 1, "application_id": "555"})).json(), {"type": 1})

    async def test_payload_limit_and_malformed(self):
        response = await self.http.post("/webhooks/telegram", headers=self.telegram_headers, content=b"x" * (MAX_BODY + 1))
        self.assertEqual(response.status_code, 413)
        self.assertEqual((await self.send_telegram({"message": {"from": {"id": 2}}})).status_code, 400)

    async def test_ignored_group_and_edited_messages(self):
        await self.send_telegram(self.telegram("hello", "group"))
        await self.send_telegram({"update_id": 51, "edited_message": self.telegram()["message"]})
        await self.drain()
        self.assertFalse(self.lookups)

    async def test_telegram_attachment_and_reply_metadata(self):
        payload = self.telegram()
        payload["message"].update(photo=[{"file_id": "small"}, {"file_id": "large"}],
                                  reply_to_message={"message_id": 8}, forward_origin={"type": "user"})
        await self.send_telegram(payload)
        await self.drain()
        self.assertEqual(self.events[0]["attachments"][0]["id"], "large")
        self.assertEqual(self.events[0]["replied_to_message_id"], "8")
        self.assertTrue(self.events[0]["forwarded"])

    async def test_unlinked_sender_is_blocked(self):
        self.linked = False
        await self.send_telegram(self.telegram("delete a repository"))
        await self.drain()
        self.assertFalse(self.events)
        self.assertIn("Link your account", self.sends[0][2]["text"])

    async def test_link_code_uses_control_plane(self):
        self.linked = False
        await self.send_telegram(self.telegram("/link one-time"))
        await self.drain()
        self.assertTrue(self.linked)
        self.assertFalse(self.events)
        self.assertEqual(self.sends[0][2]["text"], "Your account is linked.")

    async def test_delivery_auth_and_status(self):
        self.assertEqual((await self.http.post("/v1/deliveries", json={})).status_code, 401)
        self.assertEqual((await self.http.get("/v1/deliveries/" + str(uuid4()), headers=self.service_headers)).status_code, 404)
        self.assertEqual((await self.http.get("/readyz")).status_code, 200)
        self.assertIn("DeliveryRequest", self.app.openapi()["components"]["schemas"])

    async def test_delivery_429_resumes_after_last_acknowledged_chunk(self):
        self.outcomes = [200, 429, 200]
        delivery = (await self.submit_delivery(text="a" * 4000 + "b")).json()
        await self.gateway.run_once()
        row = self.gateway.store.get(delivery["delivery_id"])
        self.assertEqual((row["state"], row["cursor"]), ("queued", 1))
        with self.gateway.store.connection() as db:
            db.execute("UPDATE jobs SET next_attempt=0")
        await self.drain()
        self.assertEqual([send[2]["text"][-1] for send in self.sends], ["a", "b", "b"])
        self.assertEqual(self.gateway.store.get(delivery["delivery_id"])["state"], "sent")

    async def test_ambiguous_sends_are_not_repeated(self):
        for outcome in (500, "timeout"):
            self.outcomes = [outcome]
            text = f"send {outcome}"
            delivery = (await self.submit_delivery(text=text)).json()
            await self.drain()
            await self.submit_delivery(text=text)
            await self.drain()
            self.assertEqual(self.gateway.store.get(delivery["delivery_id"])["state"], "unknown")
        self.assertEqual(len(self.sends), 2)

    async def test_deleted_channel_is_terminal(self):
        self.outcomes = [404]
        delivery = (await self.submit_delivery()).json()
        await self.drain()
        self.assertEqual(self.gateway.store.get(delivery["delivery_id"])["error"], "platform_404")

    async def test_store_restart(self):
        delivery = (await self.submit_delivery()).json()
        self.gateway.store.claim()
        restarted = Store(self.env["GATEWAY_DB"])
        self.assertEqual(restarted.get(delivery["delivery_id"])["state"], "unknown")

    async def test_private_destinations_rejected(self):
        for address in ("127.0.0.1", "::1", "169.254.169.254", "::ffff:127.0.0.1", "10.1.0.1"):
            with self.assertRaises(ValueError):
                check_address(address)
        self.assertEqual((await fetch_public_page("http://127.0.0.1/")).status, "content_unavailable")
        self.assertEqual((await fetch_public_page("http://[::1]/")).status, "content_unavailable")

    async def test_url_extraction_and_unicode_chunking(self):
        self.assertEqual(extract_urls("No link"), [])
        self.assertEqual(extract_urls("See https://EXAMPLE.org/path#top and https://example.org/path."),
                         ["https://example.org/path"])
        self.assertEqual(list(chunks("a🙂b", 2)), ["a", "🙂", "b"])


if __name__ == "__main__":
    unittest.main()
