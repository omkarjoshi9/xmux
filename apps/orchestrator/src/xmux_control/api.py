from fastapi import FastAPI

from .health import mount_health
from .logging import configure_logging
from .settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging()
    app = FastAPI(title="XMUX control plane", docs_url=None, redoc_url=None)
    app.state.settings = settings
    mount_health(app, settings)
    return app
