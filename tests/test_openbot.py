import hashlib
import hmac
import json
import unittest

from xmux_multiplexer.openbot import OpenBotWebhook


class Response:
    status = 202
    def __enter__(self): return self
    def __exit__(self, *args): pass


class FakeOpener:
    def __init__(self): self.requests = []
    def open(self, request, timeout):
        self.requests.append(request)
        return Response()


class OpenBotTests(unittest.TestCase):
    def test_signature_and_stable_delivery(self):
        opener = FakeOpener()
        adapter = OpenBotWebhook("https://openbot.example/hook", lambda: "whsec_test", opener=opener)
        kwargs = dict(tenant_id="tenant", task_id="task", idempotency_key="key", objective="Implement",
                      repository="repo", branch="feat/test", allowed_tools=("write",), authorize=lambda: True)
        first = adapter.submit(**kwargs)
        second = adapter.submit(**kwargs)
        self.assertEqual(first.delivery_id, second.delivery_id)
        req = opener.requests[0]
        stamp = req.get_header("X-openbot-timestamp")
        signed = stamp.encode() + b"." + first.delivery_id.encode() + b"." + req.data
        expected = hmac.new(b"whsec_test", signed, hashlib.sha256).hexdigest()
        self.assertEqual(req.get_header("X-openbot-signature"), "sha256=" + expected)
        self.assertEqual(json.loads(req.data)["data"]["branch"], "feat/test")

    def test_denied_before_sending(self):
        opener = FakeOpener()
        adapter = OpenBotWebhook("https://openbot.example/hook", lambda: "secret", opener=opener)
        with self.assertRaises(PermissionError):
            adapter.submit(tenant_id="t", task_id="t", idempotency_key="k", objective="work",
                           repository="repo", branch="b", allowed_tools=(), authorize=lambda: False)
        self.assertFalse(opener.requests)


if __name__ == "__main__":
    unittest.main()
