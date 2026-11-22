"""Recoverable PostgreSQL outbox worker."""

import os
import signal
import threading

from .db.ledger import Ledger
from .dispatch import Dispatcher, HttpMultiplexer
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
    dispatcher = Dispatcher(Ledger(settings.database_url), HttpMultiplexer(settings,
        reconcile_path=os.getenv('MULTIPLEXER_RECONCILE_PATH'),
        result_path=os.getenv('MULTIPLEXER_RESULT_PATH'),
        selection_path=os.getenv('MULTIPLEXER_SELECTION_PATH')))
    while not stopped.is_set():
        if not dispatcher.tick():
            stopped.wait(1)


if __name__ == "__main__":
    main()
