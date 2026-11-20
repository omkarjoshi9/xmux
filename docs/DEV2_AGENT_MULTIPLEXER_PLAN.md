# XMUX Dev 2 build plan — Agent multiplexer

**Owner** Swayam · **Target** Working Python backend service today

The backend stack is Python. Agree on the Python web framework, dependency management, and test runner with Dev 3 before implementation.

Build one Dev 2 service that lists authorized agents, selects a compatible agent, submits a task, and returns its remote status and result references to Dev 3. The first proof is a full local run with two deterministic mock agents. Initial coding targets are T3 Code and Open Bot. Initial assistant targets are Grok Bot, Instinct via email, and Paperclip. Confirm the exact product, version, supported interface, and test access for each installation. [Project architecture](XMUX_Project_Architecture.md)

Users can register a coding multiplexer or supported channel using its repository, API, CLI, MCP, webhook, or email details. A repository URL supplies discovery information. A working connector requires an accessible supported interface and an operation mapping for that destination. Describe supported submit, status, result, capture, delivery, and cancellation operations where applicable, and return an explicit unsupported error for unavailable operations. Keep credentials in secret references.

Dev 1 owns Reddit, X, LinkedIn, Slack, Teams, Outlook, and other channel integrations. Prefer a dedicated bot or service account when the platform supports it, using the platform's permitted account and API model. Dev 2 receives verified events and sends agent results through Dev 3.

## Feature list and build order

1. Contracts and Python entry point
2. Common adapter and two mock instances
3. Agent registry and discovery
4. Deterministic routing
5. Authorized dispatch
6. Status, results, and federated references
7. Health, failure handling, and safe fallback
8. Personal assistant adapter
9. Coding multiplexer adapter
10. End-to-end smoke test and handoff

Build 1–6 in dependency order to get the mock loop working first. Verify both real provider interfaces while building that loop. Features 8 and 9 can be built independently as soon as the adapter contract and fixtures exist. Feature 10 pulls together the service, safety checks, and whichever real providers are ready.

## Feature plans

### 1. Contracts and Python entry point

**Branch** `feat/dev2-mux-contract` · **Needs** Dev 3's workspace and shared contract decisions

- Agree with Dev 1 on intent names and with Dev 3 on request, receipt, authorization, status, and error fixtures. Use the team's Python project setup and test runner. Put shared schemas and fixtures in `packages/contracts` with Dev 3.
- Define dispatch input with a task ID, stable idempotency key, intent, required capabilities, optional target, payload, and trusted principal, tenant, and approval scope. Define receipt with agent ID, remote task or session ID, provider state, and correlation metadata. Agree on normalized errors and which outcomes are retryable.
- Proposed service routes follow the work orders. Use `GET /v1/agents` and `GET /v1/agents/{id}/capabilities` for discovery, `POST /v1/dispatch` for dispatch, and `GET /v1/dispatch/{task_id}/status` for status. Agree with Dev 3 before publishing the contract because the architecture uses `/internal/agents/dispatch`. For status, agree how Dev 3 passes the persisted owner reference and authorized caller context to Dev 2.
- **Done when** The Python multiplexer entry point starts and shared fixtures validate one accepted request plus malformed and unauthorized cases. If shared workspace setup is still pending, use local draft fixtures to exercise the mock and review them with Dev 3 before exposing a shared interface.

### 2. Common adapter and two mock instances

**Branch** `feat/dev2-mock-adapter` · **Needs** 1

- In `packages/agent-adapters`, define `AgentAdapter` methods for capabilities, health, submit, status, and result. Define optional cancellation, schedule creation, and memory lookup. Model capabilities by what each configured adapter actually supports.
- Create one deterministic fake implementation with a personal research/summary configuration and a coding configuration. Make it return stable remote references and fixture results that a fresh service instance can resolve. Add controlled failures for offline, auth, rate limit, missing capability, and timeout after possible acceptance.
- **Done when** Both configurations satisfy the same interface, return retrievable results, and let a test count provider submissions. The mock gives model-sized tasks fixed inputs and expected outputs.

### 3. Agent registry and discovery

**Branch** `feat/dev2-agent-registry` · **Needs** 1, 2

- In `apps/multiplexer`, validate configured agent ID, class, transport, endpoint or executable reference, credential reference, and adapter type. Reject duplicate IDs and invalid configurations. Register both mock instances in the local test config.
- Implement authenticated list and capability routes using authorized agent IDs. Publish capability flags and availability. Resolve credential values in the adapter.
- **Done when** An authorized caller can discover both mocks, invalid config fails at startup, and responses omit secrets and agents outside caller scope.

### 4. Deterministic routing

**Branch** `feat/dev2-agent-routing` · **Needs** 3

- In `packages/routing-engine`, write a pure selector over registry descriptors, task requirements, and caller policy. Check a named target for authorization, capability and availability. Return a typed rejection when that target fails.
- For automatic selection, filter by authorization, capability, and availability. Rank by configured preference, then stable agent ID. Route research/summary to the personal class and code changes to the coding class. A recurring task requires scheduling support. Return the agent ID and reason codes, or a typed no-candidate error.
- **Done when** Tests cover explicit preference over ranking, unauthorized or unsuitable targets, route by intent, stable ties, and an unsupported schedule.

### 5. Authorized dispatch

**Branch** `feat/dev2-task-dispatch` · **Needs** 2, 4

- In `apps/multiplexer`, authenticate Dev 3, validate the request against feature 1, call the selector, and submit to the selected adapter. Keep captured content separate from trusted authorization fields. Enforce the approved repo, branch, and tool scope for coding writes before calling the provider.
- Forward stable task, correlation, and idempotency identifiers. Return the normalized receipt and provider facts for Dev 3 to persist. Dev 3 serializes durable dispatch attempts. For adapters that support idempotency, pass the same provider key on a safe retry.
- **Done when** An authenticated research request and an approved coding request reach their respective mocks. Missing approval, a scope mismatch, and unauthorized service credentials produce zero provider submissions.

### 6. Status, results, and federated references

**Branch** `feat/dev2-remote-results` · **Needs** 5

- Look up status and results through the owning adapter using Dev 3's persisted remote reference and authorized principal/tenant context. Return normalized provider status, available result references, and supported-operation errors. Cover both a synchronous result and an asynchronous receipt.
- Define owner-scoped memory and schedule references. Gate optional cancellation, schedule creation, and memory lookup on declared support. Return remote references from supported operations. Dev 3 owns the canonical task and routine records.
- **Done when** A fresh multiplexer instance can read status and results using the persisted reference, cross-tenant access fails before a provider call, and unavailable optional operations return a typed unsupported error.

### 7. Health, failure handling, and safe fallback

**Branch** `feat/dev2-dispatch-safety` · **Needs** 5, 6

- Use bounded health and provider call timeouts. Normalize auth failure, rate limit, remote failure, and timeout. Add a per-agent circuit breaker with a cooldown and recovery probe. Feed availability into automatic routing.
- Allow fallback only for policy-eligible automatic routing before submission or after confirmed rejection without side effects. Return an unknown-dispatch outcome with correlation data if the provider may have accepted the task. Dev 3 reconciles that attempt through a supported lookup or review.
- **Done when** Tests cover probe timeout, circuit recovery, safe fallback, retrying an idempotent submission, and a timeout after possible acceptance with exactly one provider submission.

### 8. Personal assistant adapters

**Branch** `feat/dev2-personal-adapter` · **Needs** 2 and verified provider access

- Confirm Grok Bot's supported programmatic interface and the exact Paperclip deployment/version. Implement adapters only against verified operations. Paperclip may serve as an orchestration destination through its API.
- For Instinct, resolve each recipient address from the authorized tenant and principal. Swayam's address is personal configuration, not a system-wide destination. Use a dedicated authorized mailbox, thread and message correlation, delivery receipts, incoming-reply verification, and duplicate handling with Dev 1 and Dev 3. Store the selected recipient with each task so replies remain tied to the original owner. Treat email acceptance as delivery, and mark task completion only when a correlated response is received.
- **Done when** Each supported adapter passes fixture tests and returns a verifiable task or message reference and result. Record unavailable interfaces or credentials and keep corresponding mock coverage.

### 9. Coding multiplexer adapter

**Branch** `feat/dev2-coding-mux-adapter` · **Needs** 2 and a team-approved provider with test access

- Create configurable API, CLI, and MCP transport adapters with explicit request and response mappings, capability declarations, credential references, timeouts, and versioned configuration. Verify repository sources and supported interfaces before registration. Never execute a repository merely because its URL is supplied.
- Add T3 Code and [Open Bot](https://github.com/nightly-labs/openbot) profiles after verifying the exact installations and interfaces. Open Bot's site is [openbot.run](https://openbot.run/). Map approved repo, branch, objective, and allowed tools to each supported provider. Map its run ID, progress, and available PR, commit, or test artifact references back to the common contract.
- **Done when** Fixture tests cover supported transports and provider profiles. Each available real provider completes an approved task on a test repo and returns a verifiable run ID and artifact. Record missing interface or access and use the coding mock while blocked.

### 10. End-to-end smoke test and handoff

**Branch** `feat/dev2-mux-smoke-test` · **Needs** 1–7 for the mock demo, plus 8 and 9 for real-provider sign-off

- In `tests/multiplexer`, use a test client that acts as Dev 3. List agents, submit personal research, fetch the result, deny a coding request without approval, submit approved coding work, and fetch its artifact references. Restart the multiplexer and repeat a status lookup using the saved receipt. Exercise unknown dispatch and verify one submission.
- Publish runnable mock commands, a short capability list for each configured adapter, request/receipt fixtures for Dev 3, and one small adapter example. Use the team's CI runner for the mock suite. Smoke-test real providers separately with test credentials.
- **Done when** The Python service starts and at least 15 meaningful automated cases pass. Dev 3 can reproduce the sample flow. Mark the real-provider MVP complete once both real smoke tests pass. An integrated demo with mocks completes the local build and leaves any blocked live checks visible.

## Branch workflow

Use one branch per feature or bug. Features use `feat/<kebab-case-name>`. Proposed bug prefix is `fix/<kebab-case-name>`. Start from current `main` once dependencies land, or branch from the required feature and note that dependency in the PR. Give Terra or Luna one numbered feature at a time with its dependency commit, relevant fixtures, and the listed done check. Review scope and run that check before merging.

## Ownership and source briefs

Swayam owns `apps/multiplexer`, `packages/agent-adapters`, `packages/routing-engine`, and `tests/multiplexer`. Dev 1 owns capture and replies. Dev 3 owns the coordination ledger, durable retries, approval decisions, MCP, and staging. Review shared schema changes with affected owners.

Scope comes from the Dev 2 engineering work order, `developer_2_agent_multiplexer.pdf`, pp. 1–2. Handoff boundaries follow the Dev 1 and Dev 3 work orders, `developer_1_platform_integrations.pdf` and `developer_3_orchestration_mcp.pdf`, pp. 1–2.
