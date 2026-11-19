<div align="center">

# XMUX

### Your apps. Your agents. One connected workflow.

Capture an idea wherever you find it. Put the right assistant to work. Get the result where you need it.

**Universal Personal Agent Gateway**

[How it works](#how-it-works) · [What you can do](#what-you-can-do) · [Integrations](#integrations) · [Project status](#project-status)

</div>

---

## From discovery to done

A useful post in your feed. A library worth trying. A message that needs research. Turning any of them into action usually means switching apps, copying context, and keeping track of several conversations.

XMUX connects those moments to your existing AI assistants and coding agents. Send a message, share a link, or make an MCP tool call. XMUX carries the context forward, coordinates the work, and keeps the result within reach.

> Share a technical post from Telegram. Ask Hermes to research it. Approve a prototype. Send it to a coding-agent multiplexer. Check the outcome from Claude through MCP.
>
> That's the experience we're building.

## How it works

1. **Capture** — Send a message, mention a supported bot, share content, or use an authorized MCP client.
2. **Choose** — Name an assistant or let XMUX route the task based on capabilities, preferences, and availability.
3. **Coordinate** — Follow progress across agents, with approval checkpoints before sensitive actions.
4. **Receive** — Get results, status updates, and artifact links through a supported channel or MCP client.

Your assistants keep their memory, schedules, and execution environments. XMUX connects their work through shared task references and coordination state.

## What you can do

### Turn content into useful work

Send an accessible article, discussion, or message for a summary, deeper research, or follow-up task. Source links and authorized context travel with the request.

### Bring the right agent to each step

Use a research assistant to evaluate an idea, then hand an approved implementation task to a coding multiplexer. Each destination handles the work it supports.

### Pick up where you left off

Start a task in a messaging app and check its status from an authorized MCP client. Track which agent owns the work and find the resulting references in one place.

### Keep control of consequential actions

Review approval checkpoints before actions such as repository changes or outbound posting. Captured posts and documents are treated as source material; they cannot grant permissions.

### Delegate recurring work

Ask a supported assistant to own a routine. XMUX tracks the remote schedule reference while that assistant manages execution.

## Integrations

XMUX is in early development. The initial release targets a focused set of connections:

| Connection                    | Planned experience                                                                                                    |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| **Telegram**                  | Capture requests and receive results through a bot                                                                    |
| **Discord**                   | Create tasks through supported commands, mentions, and messages                                                       |
| **Personal assistants**       | Delegate research and other supported tasks; Hermes is the first integration target                                   |
| **Coding-agent multiplexers** | Hand off approved coding work and track status and artifacts                                                          |
| **MCP clients**               | Capture content, discover agents, delegate work, approve actions, and query status from compatible authorized clients |

Future connections under consideration include Slack, GitHub, browser sharing, and additional messaging and social platforms. Availability depends on official APIs, permissions, and supported agent interfaces.

## Built around trust and continuity

- **Your agents stay yours.** Long-term memory, schedules, and execution artifacts remain with the owning assistant.
- **Access stays explicit.** XMUX uses authorized content and supported interfaces. When content is unavailable, it reports the limitation.
- **Capabilities stay honest.** An integration only exposes operations its destination actually supports.
- **Work stays traceable.** Task references connect the original request, destination agent, progress, and outcome.
- **Recovery respects side effects.** Persistent task state and dispatch reconciliation are designed to prevent duplicate work after interruptions.
- **Identity follows permissions.** Verified account linking and authorization govern access across channels.

## Project status

**XMUX is currently in architecture and MVP development.** The workflows and integrations described here are the product direction; a runnable release is not yet available.

The first milestone is a complete loop: capture a request, delegate it to an agent, track it reliably, and deliver a result. Subsequent milestones add cross-agent workflows, approvals, authorized MCP access, and delegated routines.

Setup instructions and integration guides will arrive alongside the working release.

## Contributing

Ideas, bug reports, and integration proposals are welcome through [GitHub Issues](https://github.com/omkarjoshi9/xmux/issues). For a new integration, describe the supported API or CLI, available permissions, and the workflow it would enable.

---

<div align="center">

**Discover anywhere. Coordinate through XMUX.**

</div>
