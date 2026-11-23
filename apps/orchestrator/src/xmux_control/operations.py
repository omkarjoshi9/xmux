"""Private, bounded metrics and fail-closed request limits."""
import hmac
import time
from uuid import uuid4

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class RequestLimits:
    def __init__(self, app, maximum=262144):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        data = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            data.extend(message.get('body', b''))
            if len(data) > self.maximum:
                response = JSONResponse({'schema_version': '1', 'code': 'payload_too_large',
                    'message': 'Request exceeds size limit', 'correlation_id': str(uuid4()),
                    'retry_after_seconds': None}, status_code=413)
                await response(scope, receive, send)
                return
            if not message.get('more_body', False):
                break
        delivered = False

        async def buffered_receive():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {'type': 'http.request', 'body': bytes(data), 'more_body': False}

        await self.app(scope, buffered_receive, send)


def mount_operations(app, metrics_token, ledger=None):
    registry = CollectorRegistry()
    count = Counter('xmux_http_requests_total', 'HTTP responses', ['status'], registry=registry)
    duration = Histogram('xmux_http_duration_seconds', 'HTTP request duration', registry=registry)

    depth = Gauge('xmux_outbox_jobs', 'Durable jobs by state', ['state'], registry=registry)
    task_count = Gauge('xmux_tasks', 'Tasks by state', ['state'], registry=registry)
    age = Gauge('xmux_oldest_pending_seconds', 'Oldest pending queue age', registry=registry)

    @app.middleware('http')
    async def measure(request, call_next):
        started = time.monotonic()
        try:
            response = await call_next(request)
            count.labels(str(response.status_code)).inc()
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Cache-Control'] = 'no-store'
            return response
        finally:
            duration.observe(time.monotonic() - started)

    @app.get('/internal/metrics', include_in_schema=False)
    def metrics(request: Request):
        provided = request.headers.get('authorization', '')
        if not metrics_token or not hmac.compare_digest(provided, 'Bearer ' + metrics_token):
            return Response(status_code=401)
        if ledger:
            import psycopg
            try:
                with ledger.connect() as conn:
                    depth.clear()
                    task_count.clear()
                    for row in conn.execute('SELECT state,count(*) AS n FROM outbox GROUP BY state'):
                        depth.labels(row['state']).set(row['n'])
                    for row in conn.execute('SELECT state,count(*) AS n FROM tasks GROUP BY state'):
                        task_count.labels(row['state']).set(row['n'])
                    row = conn.execute("SELECT COALESCE(EXTRACT(EPOCH FROM now()-min(created_at)),0) AS age "
                                       "FROM outbox WHERE state IN ('PENDING','CLAIMED')").fetchone()
                    age.set(float(row['age']))
            except psycopg.Error:
                return Response(status_code=503)
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
