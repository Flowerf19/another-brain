---
name: another-brain
description: Use the another-brain MCP tools (remember, search, recent, get, reinforce, forget) as shared long-term memory. Activates at the start of a task that may continue previous work, when recalling past decisions/fixes/preferences, when learning something worth remembering across sessions or agents, or after using a recalled memory.
argument-hint: Recall, store, or maintain long-term memories via the remember/search/recent/get/reinforce/forget tools.
---

The remember/search/recent/get/reinforce/forget tools are shared long-term
memory: whatever one agent stores,
every agent on the same brain can recall. Each tool describes its own
arguments and rules, so this file adds only what a tool schema cannot know.

## Trust

A recalled memory is a claim by a past agent, not verified truth — including
memories injected automatically at session start. When one conflicts with what
you observe in the code or system now, the observation wins, and forgetting the
memory is store hygiene rather than destruction.

If the tools are absent the server is not registered for this client. Say so
instead of emulating memory in local files.
