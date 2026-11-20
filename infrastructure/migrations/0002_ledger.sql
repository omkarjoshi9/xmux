CREATE TABLE principals (
 tenant_id uuid NOT NULL, id uuid NOT NULL, display_label text NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (tenant_id,id)
);
CREATE TABLE channel_identities (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, principal_id uuid NOT NULL,
 platform text NOT NULL, platform_user_id text NOT NULL, verified boolean NOT NULL DEFAULT false,
 FOREIGN KEY (tenant_id,principal_id) REFERENCES principals(tenant_id,id),
 UNIQUE (tenant_id,platform,platform_user_id)
);
CREATE TABLE channel_events (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, principal_id uuid NOT NULL,
 platform text NOT NULL, external_event_id text NOT NULL, fingerprint text NOT NULL,
 payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (tenant_id,principal_id) REFERENCES principals(tenant_id,id),
 UNIQUE(platform,external_event_id), UNIQUE(tenant_id,principal_id,id)
);
CREATE TABLE workflows (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, principal_id uuid NOT NULL,
 state text NOT NULL DEFAULT 'RUNNING', created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (tenant_id,principal_id) REFERENCES principals(tenant_id,id),
 UNIQUE(tenant_id,principal_id,id)
);
CREATE TABLE tasks (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, principal_id uuid NOT NULL,
 source_event_id uuid, workflow_id uuid, intent text NOT NULL, objective text NOT NULL,
 request jsonb NOT NULL, state text NOT NULL CHECK(state IN
 ('RECEIVED','REJECTED','QUEUED','AWAITING_APPROVAL','DISPATCHING','RETRY_PENDING','RUNNING','UNKNOWN','SUCCEEDED','FAILED','CANCELLED')),
 version bigint NOT NULL DEFAULT 0, assigned_agent text, result jsonb, error_code text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (tenant_id,principal_id) REFERENCES principals(tenant_id,id),
 FOREIGN KEY (tenant_id,principal_id,source_event_id) REFERENCES channel_events(tenant_id,principal_id,id),
 FOREIGN KEY (tenant_id,principal_id,workflow_id) REFERENCES workflows(tenant_id,principal_id,id),
 UNIQUE(tenant_id,principal_id,id)
);
CREATE TABLE idempotency_records (
 tenant_id uuid NOT NULL, principal_id uuid NOT NULL, operation text NOT NULL, key text NOT NULL,
 fingerprint text NOT NULL, resource_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,principal_id,operation,key),
 FOREIGN KEY(tenant_id,principal_id,resource_id) REFERENCES tasks(tenant_id,principal_id,id)
);
CREATE TABLE remote_jobs (
 id uuid PRIMARY KEY, task_id uuid NOT NULL REFERENCES tasks(id), agent_id text NOT NULL,
 remote_task_id text, remote_schedule_id text, correlation_id uuid NOT NULL UNIQUE,
 receipt jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(task_id),
 UNIQUE(agent_id,remote_task_id)
);
CREATE TABLE dispatch_attempts (
 id uuid PRIMARY KEY, task_id uuid NOT NULL REFERENCES tasks(id), correlation_id uuid NOT NULL,
 state text NOT NULL, evidence jsonb, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE approvals (
 id uuid PRIMARY KEY, task_id uuid NOT NULL REFERENCES tasks(id), action_digest text NOT NULL,
 state text NOT NULL CHECK(state IN ('PENDING','APPROVED','DENIED','EXPIRED')),
 expires_at timestamptz NOT NULL, decided_at timestamptz, decided_by uuid,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX pending_approval ON approvals(task_id) WHERE state='PENDING';
CREATE TABLE deliveries (
 id uuid PRIMARY KEY, task_id uuid NOT NULL UNIQUE REFERENCES tasks(id),
 state text NOT NULL DEFAULT 'PENDING', request jsonb NOT NULL, receipt jsonb, error_code text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE workflow_dependencies (
 task_id uuid NOT NULL REFERENCES tasks(id), depends_on uuid NOT NULL REFERENCES tasks(id),
 PRIMARY KEY(task_id,depends_on), CHECK(task_id<>depends_on)
);
CREATE TABLE outbox (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, task_id uuid NOT NULL REFERENCES tasks(id),
 kind text NOT NULL, dedup_key text NOT NULL UNIQUE, payload jsonb NOT NULL DEFAULT '{}',
 state text NOT NULL DEFAULT 'PENDING' CHECK(state IN ('PENDING','CLAIMED','DONE','DEAD')),
 attempts integer NOT NULL DEFAULT 0, available_at timestamptz NOT NULL DEFAULT now(),
 lease_until timestamptz, lease_token uuid, last_error text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE task_transitions (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, task_id uuid NOT NULL REFERENCES tasks(id),
 previous_state text, next_state text NOT NULL, actor text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE audit_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id uuid NOT NULL, principal_id uuid NOT NULL,
 task_id uuid REFERENCES tasks(id), action text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tasks_owner_time ON tasks(tenant_id,principal_id,created_at DESC,id);
CREATE INDEX tasks_state ON tasks(state,updated_at);
CREATE INDEX outbox_ready ON outbox(state,available_at,lease_until);
CREATE INDEX approvals_expiry ON approvals(expires_at) WHERE state='PENDING';
CREATE INDEX deliveries_pending ON deliveries(state,updated_at);
CREATE INDEX dispatch_correlation ON dispatch_attempts(correlation_id);

CREATE TABLE routines (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, principal_id uuid NOT NULL,
 task_id uuid NOT NULL UNIQUE, agent_id text NOT NULL, remote_schedule_id text NOT NULL,
 reference jsonb NOT NULL, state text NOT NULL, last_verified_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(tenant_id,principal_id,task_id) REFERENCES tasks(tenant_id,principal_id,id),
 UNIQUE(agent_id,remote_schedule_id)
);
CREATE INDEX routines_owner ON routines(tenant_id,principal_id);
-- Application roles should receive SELECT/INSERT only on audit history.
-- Defense against accidental UPDATE/DELETE also applies to the development owner.
CREATE FUNCTION reject_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'append-only history'; END $$;
CREATE TRIGGER immutable_transitions BEFORE UPDATE OR DELETE ON task_transitions
 FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON audit_events
 FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
