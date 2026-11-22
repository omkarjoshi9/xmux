"""Local trusted operator recovery; replay wakes reconciliation, never resets intent."""
import argparse

from .db.ledger import Conflict, Ledger


def replay(ledger, job_id, *, tenant, principal):
    with ledger.connect() as conn:
        row = conn.execute(
            "SELECT o.*,t.tenant_id,t.principal_id FROM outbox o JOIN tasks t ON t.id=o.task_id "
            "WHERE o.id=%s AND t.tenant_id=%s AND t.principal_id=%s FOR UPDATE OF o",
            (job_id, tenant, principal)).fetchone()
        if not row or row['state'] != 'DEAD':
            raise Conflict('dead_letter_not_found')
        conn.execute("UPDATE outbox SET state='PENDING',attempts=0,available_at=now(),"
                     "lease_token=NULL,lease_until=NULL WHERE id=%s", (job_id,))
        conn.execute("INSERT INTO audit_events(tenant_id,principal_id,task_id,action) VALUES(%s,%s,%s,%s)",
                     (tenant, principal, row['task_id'], 'operator.replay'))


def main():
    from .settings import Settings
    parser = argparse.ArgumentParser()
    parser.add_argument('job_id', type=int)
    parser.add_argument('--tenant', required=True)
    parser.add_argument('--principal', required=True)
    args = parser.parse_args()
    replay(Ledger(Settings.from_env().database_url), args.job_id,
           tenant=args.tenant, principal=args.principal)


if __name__ == '__main__':
    main()
