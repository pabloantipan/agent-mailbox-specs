# `discuss` — quick review

## What it is (in one line)

A **leaderless blackboard** for a cell of 4-7 named Claude Code agents per
project: peers coordinate through a project-scoped threaded mailbox, and a wake
mechanism (Stop-hook drain when busy, `asyncRewake` watcher when idle) makes them
pick up messages without a human re-prompting them.

## Community verdict

Not an oddball — it's a recognized pattern, but a minority choice.

- **It has a name.** This is the *blackboard + wake-control* pattern. Community
  write-ups describe exactly this: a blackboard needs no orchestrator; the only
  missing piece is something to wake an agent when the shared state has news.
- **The mailbox is the Claude-Code-idiomatic substrate** for local async swarms,
  and it mirrors how Anthropic's own experimental Agent Teams works internally
  (per-agent inbox files on disk). Using a DB + API instead of files fixes the
  weakness reviewers flag in the naive version (concurrency, spoofing).
- **Cell size fits mesh.** Mesh topology is the recommended shape for a known,
  small group (3-8) iterating on a shared artifact — your 4-7 roles exactly.
- **It's the minority report.** The 2026 *default* is supervisor / orchestrator-
  worker (a lead decomposes and delegates), via Agent Teams or star-heavy
  frameworks. Most people don't hand-build the wake mechanism.
- **Why the minority is right for you:** the popular frameworks are largely
  blocked on Pro/Max subscriptions (the April 2026 policy). That leaves two
  subscription-safe lanes — official Agent Teams, or DIY on first-party
  `claude -p` + hooks. You're doing the DIY lane because you want leaderless,
  persistent, cross-project cells, which Agent Teams (lead-based, one-task,
  ephemeral) doesn't give you.

## Pros

- **Truly leaderless & persistent** — no orchestrator context to bleed or
  bottleneck; each agent's session and memory persist across tasks.
- **Cheap at rest** — idle agents cost zero tokens; watchers wait as plain
  processes. You pay only per wake and per block.
- **Subscription-safe** — local service + first-party hooks; worst case is
  "it stops," never a surprise API bill.
- **Transparent & debuggable** — the whole conversation is queryable state you
  can read, export to JSONL, and replay.
- **Project isolation built in** — `project_id` on everything; three cells can't
  cross-talk.
- **Fixes the known mailbox weakness** — API-derived identity per agent stops
  spoofing; single-writer DB removes file-lock races.

## Cons / risks

- **Rests on `asyncRewake`** — real but lightly documented. If it doesn't behave
  on your version, the clean wake is gone (fallback exists, but it's tmux-ugly).
- **No reconciler.** Leaderless means nothing resolves conflicting work or
  detects a deadlock/circular wait. That's your job to design around.
- **Silent degradation.** If a watcher dies, agents don't hang — they just stop
  being woken, dropping you back to hand-review *without warning*.
- **More DIY than the median.** You're building the control plane most people get
  from Agent Teams or a framework. More power, more maintenance surface.
- **Cost is loop-shaped.** Symmetric messaging means A→B→A ping-pong is a real
  way to burn turns if unguarded.

## What to care of (the watch-list)

1. **Validate `asyncRewake` first** (see spec §6). One agent, one message, before
   21 agents. This is the load-bearing bet.
2. **Bound the wakes.** Coalesce bursts, per-agent wake rate-limit,
   `stop_hook_active` guard, `MAX_DRAINS_PER_SESSION`. Each wake/block is a
   billable turn — cap the *count* of trigger points.
3. **Supervise the watchers** and surface "watcher down for agent X" somewhere
   visible — otherwise silent degradation quietly puts you back on babysitting.
4. **Add a conflict / yield protocol** (`kind: claim` / `kind: yield`). Community
   table-stakes for leaderless peers editing shared code: one agent yields
   explicitly, with a reason, recorded in the log.
5. **Isolate files with git worktrees** — one per agent. The mailbox coordinates
   *intent*; worktrees stop them fighting over one working tree.
6. **Fail-open everywhere in the hook path.** A dead API/socket must idle the
   agent, never freeze it (`--max-time 3`, exit 0 on error).
7. **Identity, not trust.** Server derives `from` from the token; never trust a
   client-supplied sender. A prompt-injected agent is user-level trust, not
   higher.
8. **Keep dashboard overflow OFF, no `ANTHROPIC_API_KEY`** in any agent env — the
   two guarantees that keep everything on the subscription.
9. **Mind the `max_turns` gap** — hooks may not fire if a session hits its
   turn limit, since it ends before the hook runs. Matters for long autonomous
   runs.

## Bottom line

Suitable and well-precedented; deliberately in the minority; correct for you
because your billing constraint eliminates the majority tooling. Ship it once
`asyncRewake` is verified and the yield-protocol + worktree isolation are in —
those two are what a lead would otherwise have handled for you.
