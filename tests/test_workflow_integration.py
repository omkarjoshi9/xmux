from uuid import uuid4

import pytest
from control_plane.conftest import database_url, owner, task_request  # noqa: F401
from control_plane.test_dispatch import MockAgent, only_job
from psycopg.types.json import Jsonb
from xmux_control.approvals import Approvals
from xmux_control.auth import Actor
from xmux_control.dispatch import Dispatcher
from xmux_control.workflows import Workflows


@pytest.mark.parametrize('decision', ['approve', 'deny', 'expire', 'changed', 'callback'])
def test_research_to_coding(owner, task_request, decision):  # noqa: F811
    ledger, tenant, principal = owner
    actor = Actor(tenant, principal, frozenset({'tasks:create', 'approvals:decide'}), 'user')
    workflows = Workflows(ledger)
    workflow = workflows.create(actor, [
        {'name': 'research', 'request': {**task_request, 'requested_agent': 'mock-research'}},
        {'name': 'coding', 'depends_on': ['research'], 'request': {
            **task_request, 'intent': 'delegate', 'requested_agent': 'mock-code'}}], str(uuid4()))
    remote = MockAgent()
    dispatcher = Dispatcher(ledger, remote)
    with ledger.connect() as conn:
        tasks = conn.execute('SELECT * FROM tasks WHERE workflow_id=%s', (workflow,)).fetchall()
    research = next(t for t in tasks if t['intent'] == 'research')
    coding = next(t for t in tasks if t['intent'] == 'delegate')
    assert only_job(ledger, coding['id']) is None
    dispatcher.process(only_job(ledger, research['id']))
    workflows.tick()
    assert only_job(ledger, coding['id']) is None
    if decision == 'callback':
        from xmux_control.dispatch import utcnow
        from xmux_control.observations import record_callback
        record_callback(ledger, actor, {
            'schema_version': '1', 'callback_id': str(uuid4()), 'sequence': 1,
            'emitted_at': utcnow(), 'kind': 'result',
            'result': remote.result(next(iter(remote.jobs)))})
        decision = 'approve'
    else:
        dispatcher.process(only_job(ledger, research['id']))
    workflows.tick()
    assert only_job(ledger, coding['id']) is None
    with ledger.connect() as conn:
        approval = conn.execute('SELECT * FROM approvals WHERE task_id=%s', (coding['id'],)).fetchone()
        if decision == 'expire':
            conn.execute("UPDATE approvals SET expires_at=now()-interval '1 second' WHERE id=%s", (approval['id'],))
    approvals = Approvals(ledger)
    if decision == 'expire':
        approvals.expire()
    else:
        approvals.decide(actor, approval['id'], approval['action_digest'], 'deny' if decision == 'deny' else 'approve')
    if decision == 'changed':
        with ledger.connect() as conn:
            task = ledger.get_task(tenant, principal, coding['id'], connection=conn)
            conn.execute('UPDATE tasks SET request=%s WHERE id=%s',
                         (Jsonb({**task['request'], 'objective': 'Changed action'}), coding['id']))
        dispatcher.process(only_job(ledger, coding['id']))
        assert ledger.get_task(tenant, principal, coding['id'])['state'] == 'AWAITING_APPROVAL'
        assert remote.calls == 1
        return
    if decision == 'approve':
        job = only_job(ledger, coding['id'])
        _, payload = dispatcher.prepare(job)
        assert payload['task']['context_refs'] == ['artifact:test']
        assert payload['delegation']['approval_refs'] == [str(approval['id'])]
        dispatcher.accept(job, payload['attempt'], remote.submit(payload))
        dispatcher.process(only_job(ledger, coding['id']))
    workflows.tick()
    with ledger.connect() as conn:
        state = conn.execute('SELECT state FROM workflows WHERE id=%s', (workflow,)).fetchone()['state']
    assert state == ('SUCCEEDED' if decision == 'approve' else 'FAILED')
    assert remote.calls == (2 if decision == 'approve' else 1)
