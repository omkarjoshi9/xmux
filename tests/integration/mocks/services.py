"""Local contract boundary doubles, never production gateway or routing implementations."""
import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from xmux_control.contracts import validate


def create_mock(token, scenario='complete'):
    app = FastAPI()
    app.state.jobs = {}
    app.state.deliveries = {}
    app.state.dispatch_count = 0

    @app.middleware('http')
    async def authenticate(request, call_next):
        from fastapi.responses import JSONResponse
        if request.headers.get('authorization') != 'Bearer ' + token:
            return JSONResponse({'error': 'unauthenticated'}, status_code=401)
        return await call_next(request)

    @app.post('/v1/dispatch')
    async def dispatch(request: Request):
        data = await request.json()
        validate('DispatchRequest', data)
        attempt = data['attempt']
        correlation = attempt['correlation_id']
        if scenario == 'offline':
            raise HTTPException(503)
        if scenario == 'timeout_before':
            await asyncio.sleep(2)
            raise HTTPException(504)
        if correlation not in app.state.jobs:
            app.state.dispatch_count += 1
            app.state.jobs[correlation] = {'schema_version': '1', 'task_id': attempt['task_id'],
                'agent_id': attempt['agent_id'], 'correlation_id': correlation,
                'remote_task_id': str(uuid4()), 'accepted_at': datetime.now(UTC).isoformat().replace('+00:00', 'Z')}
        if scenario == 'timeout_after':
            await asyncio.sleep(2)
            raise HTTPException(504)
        return app.state.jobs[correlation]

    @app.get('/mock/reconcile/{correlation}')
    def reconcile(correlation: str):
        if correlation not in app.state.jobs:
            return {'outcome': 'UNKNOWN'}  # Absence in this process is not proof of never accepting.
        return {'outcome': 'FOUND', 'remote_ref': app.state.jobs[correlation]}

    @app.get('/mock/result/{correlation}')
    def result(correlation: str):
        ref = app.state.jobs.get(correlation)
        if not ref:
            raise HTTPException(404)
        if scenario == 'failure':
            return {'status': 'FAILED', 'remote_ref': ref}
        return {'schema_version': '1', 'status': 'SUCCEEDED', 'remote_ref': ref,
                'finished_at': datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
                'result_refs': ['mock-result:' + correlation]}

    @app.post('/v1/deliveries')
    async def deliver(request: Request):
        data = await request.json()
        validate('DeliveryRequest', data)
        key = data['idempotency_key']
        if key not in app.state.deliveries:
            app.state.deliveries[key] = data
        return {'receipt_id': key, 'state': 'ACCEPTED'}

    return app
