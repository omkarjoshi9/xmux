"""Authorized catalogs; no routing or remote memory implementation."""
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from jsonschema import ValidationError

from xmux_control.auth import authenticate
from xmux_control.contracts import validate


def install(app, ledger, *, registry_url='', registry_token='', transport=None):
    router = APIRouter()

    @router.get('/v1/identity')
    def identity(actor=Depends(authenticate)):
        return {'tenant_id': str(actor.tenant), 'principal_id': str(actor.principal),
                'scopes': sorted(actor.scopes)}

    @router.get('/v1/agents')
    async def agents(actor=Depends(authenticate)):
        actor.require('agents:read')
        if not registry_url or not registry_token:
            raise HTTPException(503, 'unavailable')
        try:
            async with httpx.AsyncClient(transport=transport, timeout=10, follow_redirects=False) as client:
                async with client.stream('GET', registry_url, headers={
                    'Authorization': 'Bearer ' + registry_token,
                    'X-Xmux-Tenant': str(actor.tenant), 'X-Xmux-Principal': str(actor.principal)}) as response:
                    response.raise_for_status()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 65536:
                            raise ValueError()
                    result = json.loads(data)
                    validate("AgentList", result)
                    return result
        except (httpx.HTTPError, ValueError, ValidationError):
            raise HTTPException(503, 'unavailable') from None

    @router.get('/v1/references/search')
    def references(query: str = Query(min_length=1, max_length=2000),
                   limit: int = Query(default=20, ge=1, le=100), actor=Depends(authenticate)):
        actor.require('references:read')
        with ledger.connect() as conn:
            rows = conn.execute('''SELECT t.id AS task_id, ref.value AS reference
                FROM tasks t CROSS JOIN LATERAL jsonb_array_elements(
                    COALESCE(t.request->'context_refs', '[]'::jsonb) ||
                    COALESCE(t.result->'result_refs', '[]'::jsonb)) ref(value)
                WHERE t.tenant_id=%s AND t.principal_id=%s
                AND strpos(lower(ref.value::text), lower(%s)) > 0
                ORDER BY t.id, ref.value::text LIMIT %s''',
                (actor.tenant, actor.principal, query, limit)).fetchall()
        return {'items': rows}

    app.include_router(router)
