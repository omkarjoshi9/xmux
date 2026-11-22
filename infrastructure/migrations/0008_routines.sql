CREATE TABLE routine_requests (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, principal_id uuid NOT NULL,
 key text NOT NULL, digest text NOT NULL, agent_id text NOT NULL, request jsonb NOT NULL,
 state text NOT NULL DEFAULT 'PENDING', remote_schedule_id text, expires_at timestamptz NOT NULL DEFAULT now()+interval '1 hour', decided_by uuid, decided_at timestamptz, last_verified_at timestamptz,
 FOREIGN KEY(tenant_id,principal_id) REFERENCES principals(tenant_id,id),
 UNIQUE(tenant_id,principal_id,key)
);
