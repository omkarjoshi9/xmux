ALTER TABLE remote_jobs ADD COLUMN capabilities jsonb;
CREATE TABLE cancellations (
 task_id uuid PRIMARY KEY REFERENCES tasks(id), id uuid NOT NULL UNIQUE,
 state text NOT NULL, remote_ref jsonb NOT NULL, receipt jsonb
);
ALTER TABLE dispatch_attempts ADD COLUMN capabilities jsonb;
