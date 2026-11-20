import unittest

from xmux_multiplexer.contracts import AgentState, AgentTask
from xmux_multiplexer.instinct_email import (
    DeliveryState, DeliveryUncertain, InboundEmail, InstinctEmailAdapter,
    OutboundEmail, SendReceipt,
)


class FakeMailbox:
    def __init__(self, uncertain=False):
        self.uncertain = uncertain
        self.sent = []
        self.inbox = []

    def send(self, message: OutboundEmail):
        self.sent.append(message)
        if self.uncertain:
            raise TimeoutError("unknown delivery")
        return SendReceipt("delivery-1", DeliveryState.ACCEPTED)

    def replies(self, correlation_id):
        return tuple(reply for reply in self.inbox if reply.correlation_id == correlation_id)


class Verifier:
    def is_verified(self, sender):
        return sender == "instinct@example.test"


class InstinctEmailTests(unittest.TestCase):
    def setUp(self):
        self.mailbox = FakeMailbox()
        self.adapter = InstinctEmailAdapter(self.mailbox, Verifier(), "instinct@example.test")
        self.task = AgentTask("task-1", "key-1", "user", "tenant", "work", "Do work")

    def test_delivery_is_not_completion_and_correlated_reply_completes(self):
        receipt = self.adapter.submit(self.task)
        self.assertEqual(self.adapter.status(receipt.remote_task_id), AgentState.RUNNING)
        self.mailbox.inbox.append(InboundEmail("m1", "instinct@example.test", receipt.remote_task_id, AgentState.COMPLETED, ("artifact://1",)))
        self.assertEqual(self.adapter.result(receipt.remote_task_id).result_refs, ("artifact://1",))

    def test_uncertain_send_is_never_blindly_retried(self):
        mailbox = FakeMailbox(uncertain=True)
        adapter = InstinctEmailAdapter(mailbox, Verifier(), "instinct@example.test")
        for _ in range(2):
            with self.assertRaises(DeliveryUncertain):
                adapter.submit(self.task)
        self.assertEqual(len(mailbox.sent), 1)

    def test_spoof_and_duplicate_replies_are_ignored_idempotently(self):
        receipt = self.adapter.submit(self.task)
        spoof = InboundEmail("bad", "attacker@example.test", receipt.remote_task_id, AgentState.COMPLETED, ("fake",))
        legit = InboundEmail("good", "instinct@example.test", receipt.remote_task_id, AgentState.COMPLETED, ("real",))
        self.mailbox.inbox.extend([spoof, legit, legit])
        self.assertEqual(self.adapter.result(receipt.remote_task_id).result_refs, ("real",))
        self.assertEqual(self.adapter.result(receipt.remote_task_id).result_refs, ("real",))


if __name__ == "__main__":
    unittest.main()
