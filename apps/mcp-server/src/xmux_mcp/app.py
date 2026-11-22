from fastapi import FastAPI
from xmux_control.health import mount_health
from xmux_control.settings import Settings


def create_app() -> FastAPI:
    app = FastAPI(title="XMUX MCP process", docs_url=None, redoc_url=None)
    mount_health(app, Settings.from_env())
    return app
