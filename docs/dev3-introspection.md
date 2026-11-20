# Dev 3 introspection integration

This is a proposed service-to-service wire contract for Dev 3 to implement and review. The shared schemas define the verified records and policy, but do not yet define this HTTP API.

Configure an HTTPS introspection URL and a separate service credential in the multiplexer host. Every request is a JSON POST with an `operation` field. Every response contains a boolean `active`. The introspection service authenticates the caller and fails closed when it cannot establish ownership, approval, grant validity, revocation, or action binding.

- `authorize_principal` receives `principal_id`, `tenant_id`, `path`, `method`, optional `task_id`, and `evidence`. It checks the caller's allowed operation and the ownership of the requested resource.
- `verify_delegation` receives principal, tenant, agent ID, the exact internal task, and the presented grant. It verifies the signature or equivalent, revocation, expiry, service identity, audience, scope, selected agent, task version, and action fingerprint. On success it returns `active: true` and `claims` containing the validated v1 `DelegationContext`. A schema-valid claim alone is insufficient.
- `verify_approval` receives principal, tenant, approval evidence and task. On success it returns `active: true` and `coding_scope` with `repository`, `branch` and `allowed_tools`. The control plane binds approval to this exact action and checks expiry.
- `authorize_provider_task` receives an agent ID, exact task and optional provider scope. It rechecks the persisted grant and provider scope before a provider operation.
- `authorize_identity_change` receives tenant, principal, recipient and proof. The proof must authorize the exact recipient for this owner.

The multiplexer validates returned delegation claims against the shared schema and checks the requested principal, tenant, task, agent, service identity, audience and expiry. Timeouts, malformed responses, and absent credentials fail closed. Secrets, full request bodies and approval evidence must not be logged.

The proposed endpoint must be aligned with Dev 3 before deployment. There is no verified production introspection endpoint in the current repository.
