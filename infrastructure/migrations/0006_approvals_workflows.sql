ALTER TABLE tasks ADD COLUMN workflow_started boolean NOT NULL DEFAULT false;
CREATE INDEX workflow_tasks ON tasks(workflow_id);
ALTER TABLE workflows ADD COLUMN creation_key text, ADD COLUMN creation_digest text;
CREATE UNIQUE INDEX workflow_creation_key ON workflows(tenant_id,principal_id,creation_key);
