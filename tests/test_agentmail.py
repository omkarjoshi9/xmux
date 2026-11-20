import io
import json
import unittest

from xmux_multiplexer.agentmail import AgentMailClient


class FakeOpener:
    def __init__(self):
        self.requests = []
    def open(self, request, timeout):
        self.requests.append(request)
        return io.BytesIO(json.dumps({"message_id": "m1", "thread_id": "t1"}).encode())


class AgentMailTests(unittest.TestCase):
    def test_send_uses_inbox_and_idempotency_header(self):
        opener = FakeOpener()
        client = AgentMailClient("secret", opener=opener)
        self.assertEqual(client.send("bot@agentmail.to", "instinct@example.org", "Hi", "Text", "task-1")["thread_id"], "t1")
        req = opener.requests[0]
        self.assertIn("/v0/inboxes/bot%40agentmail.to/messages/send", req.full_url)
        self.assertEqual(req.get_header("Idempotency-key"), "task-1")
        self.assertEqual(json.loads(req.data)["to"], "instinct@example.org")

    def test_create_inbox_and_thread_paths(self):
        opener = FakeOpener()
        client = AgentMailClient("secret", opener=opener)
        client.create_inbox("xmux-instance")
        client.get_thread("bot@agentmail.to", "thread")
        self.assertEqual(json.loads(opener.requests[0].data)["client_id"], "xmux-instance")
        self.assertTrue(opener.requests[1].full_url.endswith("/threads/thread"))

    def test_missing_key_rejected(self):
        with self.assertRaises(ValueError):
            AgentMailClient("")


if __name__ == "__main__":
    unittest.main()
