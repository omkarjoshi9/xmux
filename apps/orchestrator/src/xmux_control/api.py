import base64
import hashlib
import hmac
import json
from uuid import UUID, uuid4

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from jsonschema import ValidationError

from xmux_control.auth import Actor, authenticate
from xmux_control.contracts import validate
from xmux_control.db.ledger import Conflict, Ledger, NotFound
from xmux_control.health import mount_health
from xmux_control.logging import configure_logging
from xmux_control.observations import record_callback
from xmux_control.settings import Settings


def task_status(row):
    return {'schema_version': '1', 'task_id': str(row['id']), 'status': row['state'],
            'assigned_agent': row['assigned_agent'], 'remote_task_id': row.get('remote_task_id'),
            'created_at': row['created_at'].isoformat().replace('+00:00', 'Z'), 'updated_at': row['updated_at'].isoformat().replace('+00:00', 'Z'),
            'error_code': row['error_code'], 'result_refs': (row['result'] or {}).get('result_refs', [])}


def create_app(settings=None):
    configure_logging()
    settings = settings or Settings.from_env()
    app = FastAPI(docs_url=None, redoc_url=None)
    app.state.settings = settings
    ledger = Ledger(settings.database_url)
    app.state.ledger = ledger
    mount_health(app, settings)

    def error(status, code):
        body = {'schema_version': '1', 'code': code, 'message': code.replace('_', ' '),
                'correlation_id': str(uuid4()), 'retry_after_seconds': None}
        if code == 'validation_failed':
            body['violations'] = [{'path': '', 'reason': 'variant'}]
        return JSONResponse(body, status_code=status)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error(exc.status_code, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def route_validation_error(request, exc):
        return error(422, 'validation_failed')

    @app.exception_handler(NotFound)
    async def not_found(request, exc):
        return error(404, 'not_found')

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return error(409, 'idempotency_conflict')

    @app.exception_handler(psycopg.Error)
    async def database_error(request, exc):
        return error(503, 'unavailable')

    async def body(request, schema):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > settings.max_request_bytes:
                raise HTTPException(413, 'payload_too_large')
        try:
            value = json.loads(data)
            validate(schema, value)
            return value
        except (ValueError, ValidationError, RecursionError):
            raise HTTPException(422, 'validation_failed') from None

    @app.post('/v1/tasks')
    async def create_task(request: Request, actor: Actor = Depends(authenticate)):
        actor.require('tasks:create')
        data = await body(request, 'TaskRequest')
        if actor.role != 'user':
            # Gateway creation without a verified source event has no user ownership proof.
            raise HTTPException(403, 'forbidden')
        row, created = ledger.create_task(actor.tenant, actor.principal, data)
        return JSONResponse({'schema_version': '1', 'task_id': str(row['id']), 'status': row['state'],
                             'assigned_agent': row['assigned_agent']}, status_code=201 if created else 200)

    @app.post('/v1/events')
    async def ingest(request: Request, actor: Actor = Depends(authenticate)):
        actor.require('events:ingest')
        if actor.role != 'gateway':
            raise HTTPException(403, 'forbidden')
        event = await body(request, 'EventEnvelope')
        if UUID(event['tenant_id']) != actor.tenant:
            raise HTTPException(403, 'forbidden')
        with ledger.connect() as conn:
            identity = conn.execute('SELECT * FROM channel_identities WHERE tenant_id=%s '
                                    'AND platform=%s AND platform_user_id=%s AND verified=true',
                                    (actor.tenant, event['platform'], event['actor_external_id'])).fetchone()
        if not identity or str(identity['id']) != event['auth_context']['identity_mapping_ref']:
            raise HTTPException(403, 'forbidden')
        # Deterministic explicit commands only; external source text cannot grant permission.
        command, _, objective = event['text'].partition(' ')
        intents = {'/research': 'research', '/summarize': 'summarize', '/delegate': 'delegate'}
        if command not in intents or not objective.strip():
            raise HTTPException(422, 'validation_failed')
        task = {'schema_version': '1', 'idempotency_key': str(uuid4()), 'intent': intents[command],
                'objective': objective, 'context_refs': []}
        row, created = ledger.ingest_event(actor.tenant, identity['principal_id'], event, task)
        return JSONResponse({'schema_version': '1', 'event_id': str(row['source_event_id']),
                             'task_ids': [str(row['id'])]}, status_code=201 if created else 200)

    @app.get('/v1/tasks/{task_id}')
    def get_task(task_id: UUID, actor: Actor = Depends(authenticate)):
        actor.require('tasks:read')
        return task_status(ledger.get_task(actor.tenant, actor.principal, task_id))

    @app.get('/v1/tasks/{task_id}/result')
    def get_result(task_id: UUID, actor: Actor = Depends(authenticate)):
        actor.require('tasks:read')
        row = ledger.get_task(actor.tenant, actor.principal, task_id)
        refs = (row['result'] or {}).get('result_refs', [])
        if not refs:
            raise HTTPException(409, 'result_pending')
        return {'schema_version': '1', 'task_id': str(task_id), 'result_refs': refs}

    @app.post('/v1/tasks/{task_id}/cancel')
    def cancel(task_id: UUID, actor: Actor = Depends(authenticate)):
        actor.require('tasks:cancel')
        with ledger.connect() as conn:
            row = ledger.get_task(actor.tenant, actor.principal, task_id, connection=conn)
            if row['state'] in {'QUEUED', 'AWAITING_APPROVAL', 'RETRY_PENDING'}:
                row = ledger.transition(conn, task_id, row['state'], 'CANCELLED', str(actor.principal),
                                        version=row['version'])
            elif row['state'] not in {'CANCELLED', 'SUCCEEDED', 'FAILED', 'REJECTED'}:
                raise HTTPException(409, 'unsupported_capability')
        return task_status(row)

    @app.get('/v1/tasks')
    def list_tasks(status: str | None = None, cursor: str | None = None, limit: int = 20,
                   actor: Actor = Depends(authenticate)):
        actor.require('tasks:read')
        if not 1 <= limit <= 100:
            raise HTTPException(422, 'validation_failed')
        try:
            validate('TaskListQuery', {'limit': limit, **({'status': status} if status else {}),
                                       **({'cursor': cursor} if cursor else {})})
        except ValidationError:
            raise HTTPException(422, 'validation_failed') from None
        after = None
        if cursor:
            try:
                raw = base64.urlsafe_b64decode(cursor.encode())
                data, signature = raw[:-32], raw[-32:]
                expected = hmac.digest(settings.cursor_secret.encode(), data, 'sha256')
                if not settings.cursor_secret or not hmac.compare_digest(expected, signature):
                    raise ValueError()
                parsed = json.loads(data)
                if parsed['owner'] != f'{actor.tenant}:{actor.principal}' or parsed['status'] != status:
                    raise ValueError()
                after = UUID(parsed['after'])
            except (ValueError, KeyError):
                raise HTTPException(422, 'validation_failed') from None
        with ledger.connect() as conn:
            rows = conn.execute('SELECT * FROM tasks WHERE tenant_id=%s AND principal_id=%s '
                                'AND (%s::text IS NULL OR state=%s) AND (%s::uuid IS NULL OR id>%s) '
                                'ORDER BY id LIMIT %s',
                                (actor.tenant, actor.principal, status, status, after, after, limit+1)).fetchall()
        next_cursor = None
        if len(rows) > limit:
            if not settings.cursor_secret:
                raise HTTPException(503, 'unavailable')
            data = json.dumps({'owner': f'{actor.tenant}:{actor.principal}', 'status': status,
                               'after': str(rows[limit-1]['id'])}).encode()
            next_cursor = base64.urlsafe_b64encode(data + hmac.digest(
                settings.cursor_secret.encode(), data, hashlib.sha256)).decode()
        return {'schema_version': '1', 'items': [task_status(row) for row in rows[:limit]],
                'next_cursor': next_cursor}

    @app.post('/internal/callbacks')
    async def callback(request: Request, actor: Actor = Depends(authenticate)):
        actor.require('tasks:observe')
        data = await body(request, 'TaskCallback')
        applied = record_callback(ledger, actor, data)
        return {'accepted': True, 'applied': applied}

    return app
