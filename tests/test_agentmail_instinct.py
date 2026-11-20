import tempfile
import unittest
from pathlib import Path

from xmux_multiplexer.agentmail_instinct import AgentMailInstinct, DeliveryUncertain


class Client:
    def __init__(self): self.sent = 0; self.messages = []; self.fail = False
    def send(self, *args):
        self.sent += 1
        if self.fail: raise TimeoutError()
        return {"thread_id": "thread", "message_id": "sent"}
    def get_thread(self, *args): return {"messages": self.messages}


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "state.db")
        self.client = Client()
        self.service = AgentMailInstinct(self.client, self.path, "inbox", "instinct@example.com")
        self.args = dict(tenant="team", principal="alice", task_id="task", objective="Research")

    def test_send_persists_across_restart_and_conflicts(self):
        self.assertEqual(self.service.submit(**self.args), "thread")
        restarted = AgentMailInstinct(self.client, self.path, "inbox", "instinct@example.com")
        self.assertEqual(restarted.submit(**self.args), "thread")
        self.assertEqual(self.client.sent, 1)
        with self.assertRaises(ValueError): restarted.submit(**dict(self.args, objective="changed"))

    def test_timeout_does_not_resend(self):
        self.client.fail = True
        with self.assertRaises(DeliveryUncertain): self.service.submit(**self.args)
        with self.assertRaises(DeliveryUncertain): self.service.submit(**self.args)
        self.assertEqual(self.client.sent, 1)

    def test_reply_requires_sender_and_dmarc(self):
        self.service.submit(**self.args)
        self.client.messages = [{"message_id": "bad", "from": "instinct@example.com",
                                 "authentication_results": {"dmarc": "fail"}}]
        query = {key: self.args[key] for key in ("tenant", "principal", "task_id")}
        self.assertIsNone(self.service.result(**query))
        self.client.messages.append({"message_id": "good", "from": "instinct@example.com",
                                     "authentication_results": {"dmarc": "pass"}, "text": "Answer"})
        self.assertEqual(self.service.result(**query)["text"], "Answer")
        with self.assertRaises(LookupError): self.service.result(**dict(query, tenant="other"))


if __name__ == "__main__":
    unittest.main()
