"""Personal Hermes adapter using its documented one-shot query CLI."""
import shutil
import subprocess
from uuid import uuid4

from .contracts import AgentCapabilities, AgentResult, AgentState, AgentTask, RemoteTaskReceipt


class HermesAgent:
    """Runs ``hermes chat --query-file -`` synchronously; no remote job API implied."""
    def __init__(self, agent_id: str = "hermes", executable: str = "hermes", timeout: float = 300) -> None:
        self.id, self.executable, self.timeout = agent_id, executable, timeout
        self._results: dict[str, AgentResult] = {}

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(frozenset({"research", "general"}), supports_status=False, supports_cancel=False)

    def health(self) -> str:
        return "healthy" if shutil.which(self.executable) else "unavailable"

    def submit(self, task: AgentTask) -> RemoteTaskReceipt:
        if not task.required_capabilities.issubset(self.capabilities().operations):
            raise ValueError("unsupported_capability")
        prompt = f"{task.objective}\n\n{task.intent}"
        completed = subprocess.run([self.executable, "chat", "--query-file", "-"], input=prompt,
                                   capture_output=True, text=True, timeout=self.timeout, check=False)
        if completed.returncode:
            raise RuntimeError(f"hermes_failed:{completed.returncode}")
        # This is a process-local result handle, not a Hermes remote task ID.
        handle = f"local:{uuid4()}"
        self._results[handle] = AgentResult(AgentState.COMPLETED, output=completed.stdout)
        return RemoteTaskReceipt(self.id, handle, AgentState.COMPLETED)

    def status(self, remote_task_id: str) -> AgentState:
        raise NotImplementedError("Hermes CLI adapter does not support status")

    def result(self, remote_task_id: str) -> AgentResult:
        return self._results[remote_task_id]
