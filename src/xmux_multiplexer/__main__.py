"""Run a multiplexer configured by a trusted server-side application factory."""

import importlib
import os
from wsgiref.simple_server import make_server


def main():
    factory_path = os.environ.get("XMUX_RUNTIME_FACTORY", "")
    module_name, separator, function_name = factory_path.partition(":")
    if not separator or not module_name or not function_name:
        raise SystemExit("XMUX_RUNTIME_FACTORY must name a trusted module:function")
    factory = getattr(importlib.import_module(module_name), function_name)
    app = factory()
    if not callable(app):
        raise SystemExit("XMUX runtime factory must return a WSGI application")
    host = os.environ.get("XMUX_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("XMUX_BIND_PORT", "8080"))
    with make_server(host, port, app) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
