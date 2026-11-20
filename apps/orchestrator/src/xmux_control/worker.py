"""Worker process entrypoint; queue execution is implemented in the worker phase."""

import signal
import threading

from .health import dependencies
from .logging import configure_logging
from .settings import Settings


def main() -> None:
    settings = Settings.from_env()
    configure_logging()
    if not all(dependencies(settings).values()):
        raise SystemExit("worker dependencies unavailable")
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    stopped.wait()


if __name__ == "__main__":
    main()
