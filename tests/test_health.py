import threading
import unittest

from xmux_multiplexer.contracts import AgentTask
from xmux_multiplexer.dispatch import CircuitOpen, DispatchContext, Dispatcher, UnknownDispatch
from xmux_multiplexer.mock import MockAgent
from xmux_multiplexer.registry import AgentConfig, AgentRegistry


class HealthTests(unittest.TestCase):
    def setUp(self):
        configs = [AgentConfig(name, "https://example.org", frozenset({"research"}), frozenset({"alice"}))
                   for name in ("bad", "good")]
        self.bad = MockAgent("bad", frozenset({"research"}))
        self.good = MockAgent("good", frozenset({"research"}))
        self.dispatcher = Dispatcher(AgentRegistry(configs), {"bad": self.bad, "good": self.good},
                                     timeout_seconds=0.05, failure_threshold=1, recovery_seconds=0.05)
        self.task = AgentTask("task", "key", "alice", "tenant", "research", "Investigate")
        self.context = DispatchContext("alice", "tenant")

    def test_failed_health_falls_back_before_submission(self):
        self.bad.health = lambda: (_ for _ in ()).throw(RuntimeError("offline"))
        self.assertEqual(self.dispatcher.submit(self.task, self.context).agent_id, "good")
        with self.assertRaises(CircuitOpen):
            self.dispatcher._call("bad", lambda: True)

    def test_timeout_reports_unknown_once(self):
        started = threading.Event()
        finish = threading.Event()
        calls = []
        def slow(task):
            calls.append(task.task_id)
            started.set()
            finish.wait(2)
        self.good.submit = slow
        try:
            with self.assertRaises(UnknownDispatch):
                self.dispatcher.submit(self.task, self.context, "good")
            self.assertTrue(started.is_set())
            self.assertEqual(calls, ["task"])
        finally:
            finish.set()

    def test_half_open_allows_only_one_probe(self):
        import time
        self.dispatcher._opened["bad"] = time.monotonic() - 1
        started, finish = threading.Event(), threading.Event()
        def slow():
            started.set()
            finish.wait(2)
            return "healthy"
        thread = threading.Thread(target=lambda: self.dispatcher._call("bad", slow))
        thread.start()
        try:
            self.assertTrue(started.wait(1))
            with self.assertRaises(CircuitOpen):
                self.dispatcher._call("bad", lambda: "healthy")
        finally:
            finish.set()
            thread.join()


if __name__ == "__main__":
    unittest.main()
