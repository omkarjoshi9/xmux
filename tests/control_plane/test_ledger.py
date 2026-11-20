from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from xmux_control.db.ledger import Conflict, Ledger, NotFound
from xmux_control.db.migrate import migrate


def test_concurrent_duplicates_are_one_task_and_outbox(owner, task_request):
    ledger, tenant, principal = owner
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: ledger.create_task(tenant, principal, task_request), range(16)))
    assert sum(created for _, created in results) == 1
    assert len({row['id'] for row, _ in results}) == 1
    task_id = results[0][0]['id']
    with ledger.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM outbox WHERE task_id=%s', (task_id,)).fetchone()['n'] == 1
    with pytest.raises(Conflict, match='idempotency_conflict'):
        ledger.create_task(tenant, principal, {**task_request, 'objective': 'Different action'})


def test_rollback_leaves_no_task_or_reservation(owner, task_request):
    ledger, tenant, principal = owner
    with pytest.raises(RuntimeError):
        with ledger.connect() as conn:
            ledger.create_task(tenant, principal, task_request, connection=conn)
            raise RuntimeError('simulated process failure before commit')
    with ledger.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM tasks WHERE tenant_id=%s', (tenant,)).fetchone()['n'] == 0
    _, created = ledger.create_task(tenant, principal, task_request)
    assert created


def test_restart_owner_isolation_and_monotonic_state(owner, task_request):
    ledger, tenant, principal = owner
    row, _ = ledger.create_task(tenant, principal, task_request)
    restarted = Ledger(ledger.database_url)
    assert restarted.get_task(tenant, principal, row['id'])['state'] == 'QUEUED'
    with pytest.raises(NotFound):
        restarted.get_task(tenant, uuid4(), row['id'])
    with ledger.connect() as conn:
        ledger.transition(conn, row['id'], 'QUEUED', 'DISPATCHING', 'worker', version=0)
    with pytest.raises(Conflict):
        with ledger.connect() as conn:
            ledger.transition(conn, row['id'], 'QUEUED', 'CANCELLED', 'owner', version=0)
    with pytest.raises(Conflict):
        with ledger.connect() as conn:
            ledger.transition(conn, row['id'], 'SUCCEEDED', 'RUNNING', 'worker')


def test_outbox_recovery_fences_old_worker(owner, task_request):
    ledger, tenant, principal = owner
    row, _ = ledger.create_task(tenant, principal, task_request)
    # Use an isolated job kind so other tests' queued dispatches cannot interfere.
    with ledger.connect() as conn:
        ledger.enqueue(conn, row['id'], 'TEST', str(row['id']))
    old = ledger.claim(['TEST'], lease_seconds=-1)
    new = ledger.claim(['TEST'])
    assert old['id'] == new['id']
    assert old['lease_token'] != new['lease_token']
    with pytest.raises(Conflict, match='stale_lease'):
        with ledger.connect() as conn:
            ledger.finish(conn, old)
    with ledger.connect() as conn:
        ledger.finish(conn, new)
    assert ledger.claim(['TEST']) is None


def test_migrations_repeat_and_detect_mutation(database_url, tmp_path):
    migrate(database_url)
    (tmp_path / '0001_bootstrap.sql').write_text('SELECT 2;')
    with pytest.raises(RuntimeError, match='checksum'):
        migrate(database_url, tmp_path)


def test_event_replay_converges_and_cross_owner_collision_is_hidden(owner, task_request):
    ledger, tenant, principal = owner
    event = {'tenant_id': str(tenant), 'platform': 'telegram', 'platform_event_id': str(uuid4()),
             'text': 'Research', 'event_id': str(uuid4()), 'auth_context': {}}
    first, created = ledger.ingest_event(tenant, principal, event, task_request)
    replay, duplicate = ledger.ingest_event(tenant, principal, {**event, 'event_id': str(uuid4())}, task_request)
    assert created and not duplicate and first['id'] == replay['id']
    with pytest.raises(Conflict, match='event_conflict'):
        ledger.ingest_event(tenant, uuid4(), event, task_request)


def test_history_is_append_only(owner, task_request):
    import psycopg
    ledger, tenant, principal = owner
    row, _ = ledger.create_task(tenant, principal, task_request)
    with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
        with ledger.connect() as conn:
            conn.execute('DELETE FROM task_transitions WHERE task_id=%s', (row['id'],))
