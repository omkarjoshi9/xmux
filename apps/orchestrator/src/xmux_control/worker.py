"""Recoverable PostgreSQL outbox worker."""

import os
import signal
import threading

import httpx

from .approvals import Approvals
from .cancellation import Cancellation, HttpCancellation
from .db.ledger import Ledger
from .delivery import Delivery, HttpGateway
from .dispatch import Dispatcher, HttpMultiplexer
from .health import dependencies
from .logging import configure_logging
from .routines import Routines
from .settings import Settings
from .workflows import Workflows


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
    ledger = dispatcher.ledger
    gateway = HttpGateway(httpx.Client(base_url=settings.gateway_url,
        headers={'Authorization': 'Bearer ' + settings.gateway_token}, timeout=10),
        submit_path=os.getenv('GATEWAY_DELIVERY_PATH'), status_path=os.getenv('GATEWAY_DELIVERY_STATUS_PATH'))
    cancellation = HttpCancellation(httpx.Client(base_url=settings.multiplexer_url,
        headers={'Authorization': 'Bearer ' + settings.multiplexer_token}, timeout=10),
        cancel_path=os.getenv('MULTIPLEXER_CANCEL_PATH'), status_path=os.getenv('MULTIPLEXER_CANCEL_STATUS_PATH'))
    workers = [dispatcher, Delivery(ledger, gateway), Cancellation(ledger, cancellation)]
    while not stopped.is_set():
        Approvals(ledger).expire()
        Routines(ledger, None).expire()
        progressed = [worker.tick() for worker in workers]
        Workflows(ledger).tick()
        if not any(progressed):
            stopped.wait(1)


if __name__ == "__main__":
    main()
