# XMUX — Universal Personal Agent Gateway

> **Project name:** XMUX\
> **Status:** Architecture specification / MVP plan\
> **Architecture choice:** Federated coordinator; existing assistants
> retain their memory, schedules, and execution environments.\
> **Audience:** Three developers building a working MVP.

------------------------------------------------------------------------

## 1. Executive summary

XMUX is a **cross-platform intake, routing, and coordination
system** for personal AI assistants and coding-agent multiplexers.

You encounter a Reddit discussion, Instagram reel, LinkedIn post, X
thread, WhatsApp message, or GitHub issue. Without manually copying it
into another assistant, you mention a bot, DM a supported bot account,
share to a companion extension, or invoke an MCP tool. XMUX
receives that event, obtains content it is authorized to access,
determines the requested action, and delegates work to the appropriate
existing assistant or coding-agent multiplexer. It tracks the delegation
and returns a result or status through a supported channel.

**XMUX is not another all-purpose AI assistant.** It is the layer
that connects user-facing apps to existing assistants and coordinates
work spanning them.

### Key outcomes

1.  **Capture without context switching:** Trigger work from the app
    where the information was discovered.
2.  **One interface to multiple agents:** Select Hermes, Grok, Muse,
    Instinct, or a coding-agent multiplexer through adapters where those
    systems expose usable interfaces.
3.  **Federated ownership:** The destination assistant keeps its own
    memory, schedule, and execution; XMUX stores references and
    coordination metadata.
4.  **MCP access:** Compatible clients such as Claude or ChatGPT can
    access the same capture, delegation, and status functions, subject
    to client support and user authorization.
5.  **Cross-agent workflows:** Route research to a research agent,
    request approval, then delegate implementation to a coding
    multiplexer.
6.  **Traceability:** Know what was received, what was delegated, which
    agent owns it, and whether it finished.

### Non-goals for the MVP

-   Creating a new general-purpose LLM or agent runtime.
-   Mirroring every assistant's memory or full chat history.
-   Implementing unrestricted LinkedIn/Instagram/X/Reddit scraping.
-   Assuming all agents support persistent jobs, schedules, or
    programmatic access.
-   Building 50 integrations before validating the core
    event-to-agent-to-response loop.

------------------------------------------------------------------------

## 2. System architecture

``` mermaid
flowchart TB
  subgraph Channels["User-facing channels"]
    TG["Telegram bot"]
    DC["Discord bot"]
    SL["Slack app"]
    WA["WhatsApp Business"]
    IG["Instagram professional account"]
    OT["Reddit / X / LinkedIn adapters"]
    SHARE["Browser extension / Share-to-XMUX"]
    MCPCLIENT["ChatGPT / Claude / other MCP clients"]
  end

  subgraph D1["Developer 1 — Channel gateway"]
    WEBHOOK["Webhook / polling adapters"]
    VERIFY["Verify sender, signatures, permissions"]
    EXTRACT["Resolve authorized URLs / media / text"]
    NORMAL["Normalize event"]
    DELIVER["Outbound reply adapter"]
  end

  subgraph D3["Developer 3 — Control plane"]
    API["Ingestion and task API"]
    LEDGER[("PostgreSQL coordination ledger")]
    QUEUE["Durable queue + worker"]
    POLICY["Approval / security policy"]
    WF["Cross-agent workflow coordinator"]
    MCP["Remote MCP server"]
    STATUS["Status and result reconciliation"]
  end

  subgraph D2["Developer 2 — Agent multiplexer"]
    REG["Agent registry + capability discovery"]
    ROUTE["Selection / routing policy"]
    ADAPT["Agent adapter interface"]
    PA["Hermes / Grok / Muse / Instinct"]
    CODE["Existing coding-agent multiplexers"]
  end

  TG --> WEBHOOK
  DC --> WEBHOOK
  SL --> WEBHOOK
  WA --> WEBHOOK
  IG --> WEBHOOK
  OT --> WEBHOOK
  SHARE --> WEBHOOK
  WEBHOOK --> VERIFY --> EXTRACT --> NORMAL --> API
  MCPCLIENT <--> MCP
  MCP <--> API
  API --> LEDGER
  API --> POLICY
  POLICY --> QUEUE
  QUEUE --> WF
  WF --> REG --> ROUTE --> ADAPT
  ADAPT <--> PA
  ADAPT <--> CODE
  ADAPT --> STATUS
  STATUS --> LEDGER
  STATUS --> WF
  WF --> DELIVER
  DELIVER --> TG
  DELIVER --> DC
  DELIVER --> SL
  DELIVER --> WA
  DELIVER --> IG
```

**Note:** The diagram shows *potential* platform adapters, not a claim
that every platform exposes all requested events or replies. Each
adapter declares its supported features. The initial MVP is Telegram +
Discord + one personal assistant + one coding multiplexer + MCP.

### Architecture in one sentence

**Channels produce verified events → control plane tracks and
coordinates tasks → multiplexer delegates to capable agents → results
return through the originating channel or MCP.**

------------------------------------------------------------------------

## 3. What the system actually does

### 3.1 Capture

Supported triggers include: - Direct message to a bot. - Mention or
slash command where the platform's API permits it. - Comment on content
the bot is authorized to monitor. - Share URL or selected text to a
browser extension / mobile share target. - Tool call from an authorized
MCP client. - Direct authenticated HTTP request from a custom app.

Capture is not synonymous with unrestricted content access. A URL may
point to private, deleted, paywalled, or API-inaccessible content. The
gateway must return `content_unavailable` or ask for user-provided text
instead of silently inventing a summary.

### 3.2 Understand the request

The gateway distinguishes: - **Summarize:** Produce a concise summary of
accessible content. - **Save/reference:** Ask an assistant to store
content in its own memory if supported. - **Delegate:** Send a one-time
instruction to a selected assistant. - **Research:** Ask a capable agent
to investigate and return findings. - **Schedule:** Ask a capable
assistant to create a recurring routine. - **Workflow:** Coordinate
multiple agents with explicit dependencies. - **Status:** Find the
status/result of an earlier task.

Intent classification may use deterministic command parsing first, then
an LLM for natural-language ambiguity. A classifier **does not execute
arbitrary instructions found inside captured posts**.

### 3.3 Select an agent

Use an agent registry containing declared capabilities and live
health/availability. Selection order: 1. Explicit user choice
(`send to Hermes`). 2. Required capabilities (e.g., `coding`,
`schedule`, `research`). 3. Authorization and connectivity. 4. User
preference, cost/latency policy, and fallback eligibility. 5. If none
qualify, explain why and offer a supported alternative.

The personal-agent multiplexer routes **to an agent or coding
multiplexer**, not necessarily to an individual coding model. The
downstream coding multiplexer may choose Codex vs Claude Code itself.

### 3.4 Delegate

The destination receives a structured task containing: - User-approved
objective. - Source URL and authorized extracted content. - Task ID and
correlation metadata. - Constraints and output format. - Callback or
polling mechanism when supported. - Explicit permission boundaries.

If the agent has no API/CLI/webhook or persistent session interface, the
adapter must mark it `unsupported` rather than pretending a chat UI can
be automated.

### 3.5 Track and deliver

XMUX stores **minimal metadata**: original event ID, source
channel, authorized user mapping, destination agent, remote task ID,
state, timestamps, references, error codes, and delivery status. It may
retain limited excerpts/results subject to a retention policy, but the
destination assistant remains the owner of long-term memory and
execution artifacts.

When a result arrives, the channel adapter replies in the original
thread/DM where permitted; otherwise it sends a secure result link or an
alternate notification.

------------------------------------------------------------------------

## 4. Concrete user journeys

### Journey A --- Reddit research

1.  User mentions the bot in a Reddit context that an approved adapter
    can access, or shares the post URL.
2.  Gateway records source ID and accessible text.
3.  User requests: "Summarize the arguments and ask Hermes to
    investigate the strongest claim."
4.  Router identifies `summarize + research` and selects Hermes.
5.  Hermes returns an analysis with source links.
6.  Gateway sends a reply where authorized or exposes the result through
    MCP.

### Journey B --- Instagram reel

1.  User shares a reel to a supported professional-account messaging
    flow or share extension.
2.  Gateway attempts authorized media retrieval/transcription.
3.  If media cannot be accessed, bot requests caption/text or another
    permitted source.
4.  On successful extraction, agent receives the summary/research task.
5.  Result is returned through an available messaging channel.

### Journey C --- Coding work from a social post

1.  User sends a post describing a library: "Evaluate this for our
    project; if useful, propose a prototype."
2.  Research assistant evaluates it.
3.  Control plane creates an **approval checkpoint** before repository
    modifications.
4.  After approval, coding multiplexer receives repo, branch,
    requirements, and acceptance tests.
5.  Control plane tracks remote job and reports PR/commit/test links.
6.  Coding multiplexer owns implementation and its internal agent
    selection.

### Journey D --- Recurring routine

1.  User messages: "Every Friday, ask Hermes to review the open issues
    in Project X."
2.  Control plane selects an assistant that actually supports
    programmatic scheduling.
3.  That assistant creates the schedule and returns a remote schedule
    ID.
4.  Gateway stores the ID and ownership mapping, **not a duplicate
    schedule**.
5.  On status request, gateway asks the owning assistant for current
    state.
6.  If the assistant lacks scheduling, gateway reports the limitation or
    requests approval for an alternative scheduler.

### Journey E --- MCP

1.  Claude or ChatGPT connects to the remote MCP server through
    supported client configuration and authorization.
2.  User says: "Show the status of the research I sent from Telegram."
3.  MCP tool `get_task_status` resolves the coordination record and
    optionally queries the owning agent.
4.  Client displays the status and artifact links.

------------------------------------------------------------------------

## 5. Capability matrix and platform reality

  -----------------------------------------------------------------------------------------
  Surface           Receive events              Reply                    MVP stance
  ----------------- --------------------------- ------------------------ ------------------
  Telegram          Native bot                  Native bot reply         Build first
                    messages/commands                                    

  Discord           Native bot                  Native bot reply         Build first
                    mentions/commands/DMs with                           
                    permissions                                          

  Slack             App events/mentions/DMs     App replies              Next
                    with scopes                                          

  WhatsApp          Business Platform           Business messaging with  Later;
                    messages/webhooks           policy limits            account/approval
                                                                         needed

  Instagram         Professional-account        Restricted by            Later
                    messaging and supported     permissions/policies     
                    mention events                                       

  Reddit            Authorized API access,      Restricted/conditional   Research and later
                    moderation/bot rules                                 

  X                 API-tier-dependent          API-tier-dependent       Later
                    mentions/DMs                                         

  LinkedIn          Limited Company             No general-purpose       Share fallback;
                    Page/community-management   personal inbox bot API   conditional Page
                    events                                               API

  GitHub            GitHub App webhooks         Comments/status via      Strong follow-on
                                                authorized app           

  Browser share     Explicit user               Extension UI / result    Fallback for
                    share/selection             page                     restricted sites

  MCP client        Explicit tool calls         Tool results             MVP
  -----------------------------------------------------------------------------------------

**Never create fake human profiles or assume access to private
messages.** Verify current official API documentation and approvals
before implementation.

------------------------------------------------------------------------

## 6. Contracts shared by all three developers

### 6.1 Normalized inbound event

``` json
{
  "event_id": "evt_01J...",
  "platform": "telegram",
  "event_type": "direct_message",
  "platform_user_id": "u_123",
  "principal_id": "principal_001",
  "received_at": "2026-11-19T12:00:00Z",
  "text": "Summarize this and send to Hermes",
  "source_urls": ["https://example.org/post"],
  "attachments": [],
  "reply_target": {
    "channel_id": "chat_42",
    "message_id": "msg_7"
  },
  "permissions": ["capture", "summarize", "delegate"]
}
```

The channel adapter verifies identity and supplies `principal_id` from a
server-side mapping. It must never trust a user-supplied `principal_id`
in a message body.

### 6.2 Task submission

`POST /v1/tasks`

``` json
{
  "idempotency_key": "telegram:chat_42:msg_7",
  "principal_id": "principal_001",
  "source_event_id": "evt_01J...",
  "intent": "research",
  "requested_agent": "hermes",
  "objective": "Summarize and investigate the claim",
  "context_refs": ["content_abc"],
  "reply_target_ref": "reply_123"
}
```

Response:

``` json
{
  "task_id": "task_001",
  "state": "queued",
  "assigned_agent": null
}
```

### 6.3 Agent adapter contract

``` typescript
interface AgentAdapter {
  id: string;
  capabilities(): Promise<AgentCapabilities>;
  health(): Promise<"healthy" | "degraded" | "offline">;
  submit(task: AgentTask): Promise<RemoteTaskReceipt>;
  status(remoteTaskId: string): Promise<RemoteTaskStatus>;
  result(remoteTaskId: string): Promise<AgentResult | null>;
  cancel?(remoteTaskId: string): Promise<void>;
  createSchedule?(request: ScheduleRequest): Promise<RemoteScheduleReceipt>;
  lookupMemory?(query: MemoryQuery): Promise<MemoryReference[]>;
}
```

Optional methods **must be optional**; capability discovery must reflect
what the installed agent actually supports.

### 6.4 Task states

``` mermaid
stateDiagram-v2
  [*] --> received
  received --> rejected: invalid sender / permissions
  received --> queued: accepted
  queued --> awaiting_approval: sensitive action
  awaiting_approval --> queued: approved
  awaiting_approval --> cancelled: denied
  queued --> dispatching
  dispatching --> running: remote task accepted
  dispatching --> retry_pending: transient transport failure
  retry_pending --> dispatching: reconciled safe retry
  dispatching --> failed: permanent error
  running --> completed: verified result
  running --> failed: terminal remote failure
  running --> unknown: remote status unavailable
  unknown --> running: remote task found
  unknown --> failed: confirmed unrecoverable
  completed --> [*]
  failed --> [*]
  cancelled --> [*]
  rejected --> [*]
```

Never blindly retry a non-idempotent action. A timeout after dispatch
may mean the remote agent started successfully; reconcile by remote
task/correlation ID first.

### 6.5 Minimum API surface

  Endpoint                             Purpose                           Owner
  ------------------------------------ --------------------------------- ---------------
  `POST /v1/events`                    Verified normalized ingestion     Dev 1 → Dev 3
  `POST /v1/tasks`                     Create coordination task          Dev 3
  `GET /v1/tasks/{id}`                 Task status                       Dev 3
  `GET /v1/tasks/{id}/result`          Result or remote reference        Dev 3
  `POST /v1/tasks/{id}/approve`        Authorized approval               Dev 3
  `GET /v1/agents`                     Agent registry/capabilities       Dev 2
  `POST /internal/agents/dispatch`     Internal agent delegation         Dev 2
  `GET /internal/agents/{id}/health`   Agent availability                Dev 2
  `POST /internal/delivery`            Outbound reply job                Dev 1
  `/mcp`                               Authorized remote MCP transport   Dev 3

Internal endpoints must be authenticated and not exposed publicly.

------------------------------------------------------------------------

## 7. Data model (federated)

``` mermaid
erDiagram
  PRINCIPAL ||--o{ CHANNEL_IDENTITY : has
  PRINCIPAL ||--o{ TASK : owns
  CHANNEL_EVENT ||--o{ TASK : triggers
  TASK ||--o{ REMOTE_JOB : delegates
  TASK ||--o{ DELIVERY : produces
  TASK ||--o{ APPROVAL : may_require
  AGENT ||--o{ REMOTE_JOB : owns
  WORKFLOW ||--o{ TASK : groups
  PRINCIPAL {
    uuid id PK
    string display_label
  }
  CHANNEL_IDENTITY {
    uuid id PK
    uuid principal_id FK
    string platform
    string platform_user_id
    bool verified
  }
  CHANNEL_EVENT {
    uuid id PK
    string platform
    string external_event_id
    string content_ref
  }
  TASK {
    uuid id PK
    uuid principal_id FK
    uuid source_event_id FK
    string intent
    string state
    string idempotency_key
  }
  AGENT {
    string id PK
    string adapter_type
    json capabilities
  }
  REMOTE_JOB {
    uuid id PK
    uuid task_id FK
    string agent_id FK
    string remote_task_id
    string remote_schedule_id
  }
  WORKFLOW {
    uuid id PK
    string state
  }
  APPROVAL {
    uuid id PK
    uuid task_id FK
    string state
  }
  DELIVERY {
    uuid id PK
    uuid task_id FK
    string reply_target_ref
    string state
  }
```

The ledger holds pointers and minimal coordination state. Original agent
conversations, embeddings, schedules, and execution artifacts stay in
the agent's own store unless explicitly imported.

------------------------------------------------------------------------

## 8. Security and trust boundaries

1.  **Identity:** Map platform identities to verified principals;
    require explicit account linking for cross-platform privileges.
2.  **Webhook verification:** Verify platform signatures/tokens and
    protect against replay.
3.  **Prompt injection:** Treat posts, DMs, webpages, and documents as
    untrusted source material. Do not let their contents grant tool
    permissions.
4.  **Approval:** Require approval for code changes, outbound posting,
    purchases, deletion, and other sensitive operations according to
    policy.
5.  **Least privilege:** Use per-platform OAuth scopes and per-agent
    credentials stored in a secret manager.
6.  **MCP auth:** Require appropriate authentication/authorization;
    never expose task tools anonymously.
7.  **Data minimization:** Retain only required content; implement
    deletion, expiry, and audit events.
8.  **Isolation:** Separate public webhook endpoints from internal
    dispatch services.
9.  **Idempotency:** Deduplicate repeated webhook events and reconcile
    uncertain remote dispatches.
10. **Observability:** Log correlation IDs and state transitions, but
    redact tokens and private message content.

------------------------------------------------------------------------

## 9. Exact division of labor

### Developer 1 --- Channel gateway and ingestion

**Owns:** `apps/gateway`, `packages/channel-adapters`,
`packages/content-extractors`.

Deliver: - Telegram bot adapter with command/DM capture and threaded
replies. - Discord adapter with supported slash commands, mentions, DMs,
and replies. - Webhook verification, deduplication, sender identity
mapping. - URL parsing, accessible HTML extraction, metadata, content
failure codes. - Canonical normalized event producer. - Outbound
delivery worker and formatting (long messages, links, failures). - Mock
connector fixtures, contract tests, platform-specific integration
tests. - Slack adapter after core Telegram/Discord flow. -
Meta/X/Reddit/LinkedIn feasibility and permissions checklist, with later
adapters only where authorized.

**Done when:** Telegram and Discord events create canonical events,
rejected identities cannot run privileged tasks, and replies can be
delivered or explicitly marked undeliverable.

### Developer 2 --- Agent multiplexer

**Owns:** `apps/multiplexer`, `packages/agent-adapters`,
`packages/routing-engine`.

Deliver: - `AgentAdapter` TypeScript interface and capability schema. -
Registry, configuration validation, health probing. - Mock agent for
deterministic end-to-end tests. - Hermes adapter using a verified
supported interface. - One existing coding multiplexer adapter using its
real supported API/CLI. - Explicit routing, capability filtering, user
preferences, fallback policy. - Remote task IDs, status/result polling
or callbacks. - Federated memory/schedule references where supported;
never fake support. - Error normalization (`unavailable`,
`unsupported_capability`, `auth_failed`, `remote_failure`).

**Done when:** A task can be routed to two distinct agent classes and
the correct remote result or explicit unsupported error is returned.

### Developer 3 --- Control plane, MCP, operations

**Owns:** `apps/orchestrator`, `apps/mcp-server`,
`packages/workflow-sdk`, `infrastructure`.

Deliver: - PostgreSQL migrations for principals, events, tasks, remote
jobs, approvals, deliveries, workflows. - Authenticated task/event APIs,
idempotency and task state machine. - Durable queue, workers, retries
with remote reconciliation, dead-letter handling. - MCP tools:
`capture_content`, `delegate_task`, `get_task_status`, `list_agents`,
`approve_action`, `create_routine` (only when supported),
`search_references`. - Cross-agent dependency workflow and approval
checkpoint. - Delegated schedule creation and remote schedule reference
tracking. - Authentication, authorization, secrets, audit logging. -
Docker Compose, CI, health checks, metrics, runbook and backup policy. -
End-to-end test harness with Dev 1 and Dev 2 mocks.

**Done when:** The system survives restart during a running task, avoids
duplicate dispatch on retry, enforces approval, and exposes usable
authorized MCP tools.

### Shared contract and ownership rule

All three agree on `packages/contracts` before independent
implementation. Each developer owns their modules and mocks. Cross-team
changes require a contract PR and contract tests. No one implements
another person's service logic as a shortcut.

------------------------------------------------------------------------

## 10. Four-week implementation schedule

  -------------------------------------------------------------------------------
  Week           Developer 1    Developer 2       Developer 3      Exit condition
  -------------- -------------- ----------------- ---------------- --------------
  1              Telegram,      Agent interface,  Monorepo,        Telegram →
                 verified       mock adapter,     contracts, DB,   durable mock
                 event, mock    registry          task API         task
                 reply                                             

  2              Discord, URL   Hermes + coding   Queue, state     Real agent
                 extraction,    mux adapters,     machine, status, task → channel
                 reply delivery routing           reconciliation   reply

  3              Slack,         Second            MCP server,      MCP client
                 identity       agent/fallback,   approval,        sees and
                 linking,       federated refs    cross-agent      delegates
                 platform                         workflow         tasks
                 permission                                        
                 research                                          

  4              Hardening,     Adapter           Delegated        Demo passes
                 Meta setup if  reliability,      routines, CI,    acceptance
                 approved       capability tests  Docker,          checklist
                                                  monitoring       
  -------------------------------------------------------------------------------

**Time estimates are planning targets, not promises.** External platform
review and undocumented agent interfaces may push work into a later
phase.

------------------------------------------------------------------------

## 11. MVP acceptance tests

-   [ ] Telegram DM creates exactly one task even after duplicate
    webhook delivery.
-   [ ] Discord command creates a task and replies in the correct
    context.
-   [ ] Invalid signatures and unauthorized users are rejected.
-   [ ] URL extraction returns provenance and an explicit error when
    inaccessible.
-   [ ] Explicitly named assistant takes precedence over automatic
    routing.
-   [ ] Capability mismatch produces `unsupported_capability`, not a
    fake success.
-   [ ] Hermes receives a task and returns a verifiable result through a
    supported interface.
-   [ ] A coding multiplexer receives a task and returns status/artifact
    references.
-   [ ] A task can be queried after server restart.
-   [ ] A dispatch timeout is reconciled before retrying.
-   [ ] Sensitive workflow step waits for authorized approval.
-   [ ] A compatible authorized MCP client can list agents, delegate,
    and check status.
-   [ ] A routine is created only when the chosen agent confirms support
    and returns a remote ID.
-   [ ] The originating channel receives the result or an explicit
    delivery-failure record.
-   [ ] Logs and responses do not leak secrets or cross-user task data.

------------------------------------------------------------------------

## 12. Deployment blueprint

**Initial deployment:** Docker Compose on a VPS.

``` text
reverse-proxy
  ├── gateway-api          (public verified webhooks)
  ├── orchestrator-api     (authenticated)
  └── mcp-server           (authenticated remote MCP)
postgres
redis
gateway-worker
orchestrator-worker
multiplexer-service
observability/logs
```

Public ingress must use HTTPS. Internal services communicate on a
private network. Back up PostgreSQL and keep secrets outside source
control. Add per-channel rate limits and a simple admin-only task
inspection interface if necessary.

### Suggested monorepo

``` text
xmux/
├── apps/
│   ├── gateway/               # Dev 1
│   ├── multiplexer/           # Dev 2
│   ├── orchestrator/          # Dev 3
│   └── mcp-server/            # Dev 3
├── packages/
│   ├── contracts/             # Shared
│   ├── channel-adapters/      # Dev 1
│   ├── content-extractors/    # Dev 1
│   ├── agent-adapters/        # Dev 2
│   ├── routing-engine/        # Dev 2
│   └── workflow-sdk/          # Dev 3
├── infrastructure/            # Dev 3
├── tests/
│   ├── contracts/
│   ├── integration/
│   └── end-to-end/
└── docs/
```

------------------------------------------------------------------------

## 13. Expansion plan

**Phase 2:** Slack, GitHub App, WhatsApp Business, browser
extension/share target, richer identity linking.

**Phase 3:** Instagram professional-account messaging and supported
mentions, Reddit/X with appropriate API access, LinkedIn Company Page
events if approved, additional personal assistants.

**Phase 4:** Multi-agent workflow templates, optional centralized search
index, user dashboard, granular permissions, multi-tenant support,
analytics and cost-aware routing.

Every adapter must publish a capability manifest: - Supported inbound
triggers. - Supported outbound reply methods. - Available content
access. - Required permissions and approval. - Rate limits. - Data
retention restrictions. - Known unsupported operations.

------------------------------------------------------------------------

## 14. Open design decisions

1.  Confirm the exact programmatic interfaces for Hermes, Grok Bot,
    Muse, Instinct, and the coding multiplexer.
2.  Choose TypeScript/NestJS versus Python/FastAPI for the common
    backend; favor a single primary language where practical.
3.  Decide whether summaries are produced by a lightweight gateway LLM
    or always delegated to a personal assistant.
4.  Set a retention window for source text and task results.
5.  Define the approval policy for repo edits, outbound posts, and other
    sensitive actions.
6.  Decide whether one principal may link multiple social identities and
    how to verify ownership.
7.  Confirm which MCP clients support the intended remote authentication
    and tool workflows.
8.  Decide which agent, if any, will own routines when the explicitly
    requested assistant lacks scheduling.

------------------------------------------------------------------------

## 15. Product definition

**XMUX is a federated, cross-platform personal-agent gateway and
orchestration control plane.** It turns user-authorized messages,
mentions, shares, and MCP tool calls into structured tasks; routes them
to existing personal assistants or coding-agent multiplexers; tracks
cross-agent work without taking over their memory or execution; and
delivers results back to the user.

**North-star demo:** Share a technical post from Telegram, ask Hermes to
research it, approve a prototype, have a coding-agent multiplexer
implement it, and check the outcome from Claude through MCP---all
without manually transferring context between assistants.
