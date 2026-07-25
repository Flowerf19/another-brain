# Memory Trust Model

Memories are **claims, not facts**. Every memory in the store was written by
a fallible agent at a point in time, about a world that may have moved on.
Storage, indexing, and retrieval never verify truth — they only preserve and
rank what past agents chose to assert. Ground truth lives in code, tests,
and the current state of the system; memory is a hint about where to look.

This document is the epistemic contract of Another Brain. It exists because
recall — especially *proactive* recall (the SessionStart hook) — injects
unverified text into agent context, and unverified text read as truth is how
agents hallucinate with confidence.

## 1. Contamination vectors

| Vector | Mechanism | Primary defense |
| --- | --- | --- |
| **Authority confusion** | Auto-injected text at session start reads like system truth, not fallible notes | Epistemic framing (section 4) |
| **Stale-but-confident** | The world changed; the memory did not. Agent asserts outdated facts | Recency windows, `timeline_day` freshness evidence, TTL |
| **Contamination loop** | Wrong memory → trusted → new memories derived from it → error compounds | Conflict → `brain_forget` (section 4) |
| **Cross-agent poisoning** | One shared brain: a single agent's error or junk reaches every other agent | Writer stance (section 5), audit trail |
| **Context-free fragments** | A 1-2 sentence summary detached from its situation; the model fills the gaps | Preview-only recall, `brain_get` before acting |
| **Anchoring** | A recalled decision stops re-evaluation of a situation that has changed | "Code wins" rule |

These vectors exist in *all* recall, not just hook injection. A manual
`brain_search` returns the same unverified claims; the hook only raises
their frequency and salience. The trust model therefore applies to every
read path equally.

## 2. What the system guarantees today

- **Preview-only recall**: search/recent return summaries; detail requires
  an explicit `brain_get`. There is always a deliberate step before full
  reliance.
- **Freshness evidence**: every preview carries `timeline_day`; the hook
  injects only the last 3 days.
- **Decay**: every memory expires unless a reader explicitly judged it
  correct (`brain_reinforce`). Failure direction is forgetting.
- **Erasability**: `brain_forget` removes wrong memories from all queries
  immediately; admin restore exists for mistakes.
- **Provenance**: every record and audit event carries who wrote it and
  when — wrong memories can be traced to their author.
- **No auto-renewal**: appearing in results never extends a memory's life.

## 3. What the system deliberately does NOT guarantee

- **No truth verification** at write, index, or read time.
- **No conflict detection**: two memories may contradict; the newer does not
  override the older (append-only).
- **No authority gradient**: a memory written by a trusted agent and one
  written by a buggy agent look identical in results — only `agent_id`
  provenance distinguishes them.

## 4. Reader stance (contract for agents)

1. Treat every recalled memory as a **hypothesis**, not a fact. Verify
   against current code/docs before relying on it for anything that matters.
2. **Code wins.** When a memory conflicts with what you observe, the
   observation is right and the memory is wrong.
3. On discovering a wrong memory: `brain_forget` it. A wrong memory left in
   the store is a trap for the next agent; forgetting is store hygiene, not
   rudeness.
4. `brain_get` before acting on a preview when the action is expensive or
   irreversible.
5. `brain_reinforce` only after a memory proved correct *in use* — never on
   sight.

## 5. Writer stance (contract for agents)

1. Write **verifiable claims**: exact names, versions, commands, dates — so a
   future reader can check them cheaply.
2. One memory = one claim. Bundled claims rot at different speeds.
3. Set `importance` honestly: it controls how long a wrong memory can
   survive if never reviewed.
4. Update = `brain_remember` (new version) + `brain_forget` (old version).
   Never leave both.

## 6. Proactive recall addendum (SessionStart hook)

The hook makes section 4 load-bearing: injected memories arrive without the
agent choosing to recall, so the framing of the injection matters as much as
its content.

- The injected block must be labeled as unverified notes (see open decision
  below).
- Window stays small (days, single-digit count): recency is the cheapest
  truth proxy available.
- Silence is a valid outcome: no memories → nothing injected → zero risk.

## Open decisions

- [x] Warning on hook output — implemented at the injection point (the hook
  command in `~/.claude/settings.json` prepends an "unverified claims"
  line), deliberately NOT in the CLI: `python src/main.py recent` is a
  neutral, general-purpose output and must stay usable by other consumers.
- [x] Reader-stance text in `skills/another-brain/SKILL.md` — "claims, not
  facts; code wins; forget-on-conflict" section added.
- [ ] Whether hook injection should filter by `catalog` (e.g. inject
  `task`/`decision` only) or by `min_importance`.
- [ ] `brain_ingest` (auto-capture) must not land while these are open —
  automated writing multiplies every vector in section 1.
