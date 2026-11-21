import hashlib
from uuid import UUID

import rfc8785
from psycopg.types.json import Jsonb

from xmux_control.contracts import validate
from xmux_control.db.ledger import Conflict, NotFound


def record_callback(ledger, actor, callback):
    validate('TaskCallback', callback)
    if callback['kind'] != 'result':
        raise Conflict('unsupported_observation')
    result = callback['result']
    ref = result['remote_ref']
    task_id = UUID(ref['task_id'])
    digest = hashlib.sha256(rfc8785.dumps(callback)).hexdigest()
    with ledger.connect() as conn:
        task = conn.execute('SELECT * FROM tasks WHERE id=%s AND tenant_id=%s FOR UPDATE',
                            (task_id, actor.tenant)).fetchone()
        if not task:
            raise NotFound('task_not_found')
        job = conn.execute('SELECT * FROM remote_jobs WHERE task_id=%s', (task_id,)).fetchone()
        if not job or job['receipt'] != ref:
            raise Conflict('remote_binding_mismatch')
        previous = conn.execute('SELECT * FROM callback_receipts WHERE callback_id=%s OR '
                                '(task_id=%s AND sequence=%s)',
                                (callback['callback_id'], task_id, callback['sequence'])).fetchone()
        if previous:
            if previous['digest'] != digest:
                raise Conflict('callback_conflict')
            return False
        latest = conn.execute('SELECT max(sequence) AS sequence FROM callback_receipts WHERE task_id=%s',
                              (task_id,)).fetchone()['sequence']
        if latest is not None and callback['sequence'] <= latest:
            raise Conflict('stale_callback')
        if task['state'] == 'UNKNOWN':
            ledger.transition(conn, task_id, 'UNKNOWN', 'RUNNING', 'callback')
            task['state'] = 'RUNNING'
        if task['state'] == 'RUNNING':
            ledger.transition(conn, task_id, 'RUNNING', result['status'], 'callback')
            conn.execute('UPDATE tasks SET result=%s,error_code=%s WHERE id=%s',
                         (Jsonb(result), result.get('error', {}).get('code'), task_id))
            ledger.enqueue(conn, task_id, 'DELIVERY', f'delivery:{task_id}')
        elif task['state'] != result['status'] or task['result'] != result:
            raise Conflict('terminal_conflict')
        conn.execute('INSERT INTO callback_receipts(callback_id,task_id,sequence,digest) VALUES(%s,%s,%s,%s)',
                     (callback['callback_id'], task_id, callback['sequence'], digest))
        return True
