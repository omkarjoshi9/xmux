"""Explicit schedule boundary; endpoint owners enforce delegated identity."""
import os

import httpx
from fastapi import HTTPException

from .contracts import validate


class HttpRoutines:
    def __init__(self, settings, *, transport=None):
        self.settings, self.transport = settings, transport

    def call(self, actor, path, payload):
        if not path:
            raise HTTPException(409, 'unsupported_capability')
        if not path.startswith('/') or path.startswith('//') or '..' in path:
            raise HTTPException(503, 'unavailable')
        try:
            with httpx.Client(base_url=self.settings.multiplexer_url, transport=self.transport,
                              timeout=10, follow_redirects=False) as client:
                response = client.post(path, json=payload, headers={
                    'Authorization': 'Bearer ' + self.settings.multiplexer_token,
                    'X-Xmux-Tenant': str(actor.tenant), 'X-Xmux-Principal': str(actor.principal)})
                response.raise_for_status()
                if len(response.content) > 65536:
                    raise ValueError('oversized_response')
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError('invalid_response')
                return result
        except (httpx.HTTPError, ValueError):
            raise ConnectionError('remote_schedule_unavailable') from None

    def capabilities(self, actor, agent):
        result = self.call(actor, os.getenv('MULTIPLEXER_SCHEDULE_CAPABILITIES_PATH'), {'agent_id': agent})
        validate('AgentCapabilities', result)
        if result['agent_id'] != agent:
            raise ConnectionError('agent_binding_mismatch')
        return {'authorized': True, 'schedule_create': result['supports_schedule'],
                'schedule_lookup': result['supports_schedule'] and result['supports_correlation_lookup']}

    def bound(self, actor, agent, identity, result):
        for key, expected in {'tenant_id': str(actor.tenant), 'principal_id': str(actor.principal),
                              'agent_id': agent, 'correlation_id': identity}.items():
            if result.get(key) != expected:
                raise ConnectionError('schedule_binding_mismatch')
        return result

    def create_schedule(self, actor, agent, identity, request):
        result = self.call(actor, os.getenv('MULTIPLEXER_SCHEDULE_CREATE_PATH'),
                           {'agent_id': agent, 'correlation_id': identity, 'request': request})
        return self.bound(actor, agent, identity, result)

    def lookup_schedule(self, actor, agent, identity, remote_id):
        result = self.call(actor, os.getenv('MULTIPLEXER_SCHEDULE_LOOKUP_PATH'),
                           {'agent_id': agent, 'correlation_id': identity, 'remote_schedule_id': remote_id})
        self.bound(actor, agent, identity, result)
        if remote_id and result.get('remote_schedule_id', remote_id) != remote_id:
            raise ConnectionError('schedule_id_mismatch')
        return result
