from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from test_delivery import claim
from xmux_control.cancellation import Cancellation
from xmux_control.db.ledger import NotFound
from xmux_control.dispatch import RemoteUnavailable, utcnow


class Remote:
    calls = 0
    timeout = False

    def submit(self, payload):
        self.calls += 1
        self.payload = payload
        if self.timeout:
            raise RemoteUnavailable('timeout')
        return self.reconcile(payload['cancellation_id'])

    def reconcile(self, key):
        return {**self.payload, 'outcome': 'CONFIRMED', 'observed_at': utcnow(), 'confirmed_at': utcnow()}


@pytest.mark.parametrize('supported,timeout', [(True, False), (False, False), (True, True)])
def test_cancellation(owner, task_request, supported, timeout):
    ledger, tenant, principal = owner
    task, _ = ledger.create_task(tenant, principal, task_request)
    ref = {'schema_version': '1', 'task_id': str(task['id']), 'agent_id': 'agent',
           'correlation_id': str(uuid4()), 'remote_task_id': str(uuid4()), 'accepted_at': utcnow()}
    caps = {'schema_version': '1', 'agent_id': 'agent', 'operations': ['research'],
            **{k: False for k in ('supports_status', 'supports_schedule', 'supports_memory_lookup',
                                  'supports_callbacks', 'supports_correlation_lookup')},
            'supports_cancel': supported, 'dispatch_idempotency': {'supported': False}}
    with ledger.connect() as conn:
        conn.execute("UPDATE tasks SET state='RUNNING' WHERE id=%s", (task['id'],))
        conn.execute('INSERT INTO remote_jobs(id,task_id,agent_id,remote_task_id,correlation_id,receipt,capabilities) '
                     'VALUES(%s,%s,%s,%s,%s,%s,%s)',
                     (uuid4(), task['id'], 'agent', ref['remote_task_id'], ref['correlation_id'], Jsonb(ref), Jsonb(caps)))
    remote = Remote()
    remote.timeout = timeout
    worker = Cancellation(ledger, remote)
    with pytest.raises(NotFound):
        worker.request(tenant, uuid4(), task['id'])
    row = worker.request(tenant, principal, task['id'])
    if not supported:
        assert row['state'] == 'UNSUPPORTED'
        assert ledger.get_task(tenant, principal, task['id'])['state'] == 'RUNNING'
        return
    job = claim(ledger, task['id'], 'CANCEL')
    if timeout:
        with pytest.raises(RemoteUnavailable):
            worker.process(job)
        assert ledger.get_task(tenant, principal, task['id'])['state'] == 'RUNNING'
        worker.defer(job)
        Cancellation(ledger, remote).process(claim(ledger, task['id'], 'CANCEL'))
    else:
        worker.process(job)
    assert ledger.get_task(tenant, principal, task['id'])['state'] == 'CANCELLED'
    assert remote.calls == 1
