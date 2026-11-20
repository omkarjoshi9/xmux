import json
import unittest

from xmux_multiplexer.grokbot import (
    GrokBotWebhook, WebhookOutcomeUnknown,
)


class FakeTransport:
    def __init__(self, status=200, error=None):
        self.status, self.error, self.calls = status, error, []

    def post(self, url, headers, body, timeout):
        self.calls.append((url, headers, body, timeout))
        if self.error:
            raise self.error
        return self.status


class GrokBotWebhookTests(unittest.TestCase):
    def adapter(self, transport, scopes=frozenset({"routine:trigger"})):
        return GrokBotWebhook("https://example.test/hook", "secret://routine",
                              lambda ref: "test-key", scopes, transport)

    def test_post_headers_and_json(self):
        transport = FakeTransport()
        result = self.adapter(transport).submit({"hello": "world"})
        url, headers, body, _ = transport.calls[0]
        self.assertEqual(result.accepted, True)
        self.assertEqual(url, "https://example.test/hook")
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(json.loads(body), {"hello": "world"})
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_no_body_and_non_200_rejected(self):
        transport = FakeTransport(status=204)
        self.assertFalse(self.adapter(transport).submit().accepted)
        self.assertIsNone(transport.calls[0][2])

    def test_timeout_is_unknown_and_not_retried(self):
        transport = FakeTransport(error=TimeoutError())
        with self.assertRaises(WebhookOutcomeUnknown):
            self.adapter(transport).submit()
        self.assertEqual(len(transport.calls), 1)

    def test_scope_and_https_are_strict(self):
        with self.assertRaisesRegex(ValueError, "unsupported_scope"):
            self.adapter(FakeTransport(), frozenset({"routine:trigger", "status:read"}))
        with self.assertRaisesRegex(ValueError, "webhook_url_must_use_https"):
            GrokBotWebhook("http://example.test", "s", lambda _: "x",
                           frozenset({"routine:trigger"}))


if __name__ == "__main__":
    unittest.main()
