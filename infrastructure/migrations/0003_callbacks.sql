CREATE TABLE callback_receipts (
 callback_id uuid PRIMARY KEY, task_id uuid NOT NULL REFERENCES tasks(id),
 sequence bigint NOT NULL, digest text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(task_id,sequence)
);
