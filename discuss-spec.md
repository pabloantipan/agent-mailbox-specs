# `discuss` — implementation spec

Project-scoped threaded mailbox for a leaderless cell of Claude Code agents,
with drain-on-completion (Stop hook) and push-wake (asyncRewake watcher).
Terminal-session-per-agent; no central supervisor. Subscription-safe (local
service + first-party hooks only, never the metered API).

Three components: **DB** (§1), **Go API** (§2), **hooks** (§3). Config values in
§4, flows in §5, the verification gate in §6.

---

## 1. DB config — SQLite, embedded in the API process

The API is the single writer; agents/hooks never touch the file directly.

```
PRAGMA journal_mode = WAL;      -- concurrent readers + one writer
PRAGMA busy_timeout = 5000;     -- wait out a held write lock
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;    -- WAL-safe, faster than FULL
```

Location: `/var/lib/discuss/discuss.db` (or `~/.local/share/discuss/`).

### Schema

```sql
CREATE TABLE messages (
  id          TEXT PRIMARY KEY,      -- ULID (time-sortable)
  project_id  TEXT NOT NULL,
  thread_id   TEXT NOT NULL,
  parent_id   TEXT REFERENCES messages(id),
  from_agent  TEXT NOT NULL,
  to_agent    TEXT,                  -- NULL = broadcast to the cell
  kind        TEXT NOT NULL,         -- msg|question|answer|status|done|decision|claim|yield
  subject     TEXT,
  body        TEXT NOT NULL,
  created_at  INTEGER NOT NULL       -- unix millis
);

CREATE TABLE reads (
  project_id   TEXT NOT NULL,
  message_id   TEXT NOT NULL REFERENCES messages(id),
  agent        TEXT NOT NULL,
  delivered_at INTEGER,              -- shown by a drain/wake (loop guard)
  acked_at     INTEGER,              -- agent explicitly handled it (semantic)
  PRIMARY KEY (project_id, message_id, agent)
);

CREATE TABLE threads (
  id            TEXT PRIMARY KEY,
  project_id    TEXT NOT NULL,
  subject       TEXT,
  status        TEXT NOT NULL DEFAULT 'open',  -- open|stalled|escalated|closed
  since_decision INTEGER NOT NULL DEFAULT 0,   -- messages since last kind=decision
  created_at    INTEGER NOT NULL
);

-- session-level cost cap for the drain endpoint
CREATE TABLE session_drains (
  session_id TEXT PRIMARY KEY,
  count      INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);

CREATE INDEX idx_msg_recipient ON messages(project_id, to_agent, created_at);
CREATE INDEX idx_msg_thread    ON messages(project_id, thread_id, created_at);
CREATE INDEX idx_reads_agent   ON reads(project_id, agent);
```

### The one hot query — "undelivered for (project, agent)"

```sql
SELECT m.* FROM messages m
WHERE m.project_id = :pid
  AND (m.to_agent = :agent OR m.to_agent IS NULL)
  AND m.from_agent != :agent
  AND NOT EXISTS (
    SELECT 1 FROM reads r
    WHERE r.project_id = :pid AND r.message_id = m.id
      AND r.agent = :agent AND r.delivered_at IS NOT NULL)
ORDER BY m.created_at ASC;
```

`delivered_at` is the loop guard (a message shown once never re-triggers);
`acked_at` is separate so you can still ask "which requests are open."

### Agreement stall — the *semantic* loop guard

`MAX_DRAINS_PER_SESSION` and `WAKE_PER_MIN` bound how often an agent is woken.
They do not bound how long a *subject* stays unresolved — a cell can burn 40
messages inside the wake budget and converge on nothing. A subject with no
decision runs forever.

So `threads.since_decision` counts messages since the last `kind = decision`.
At `AGREEMENT_STALL` the server sets `status = 'stalled'` and **rejects further
posts to that thread**, then messages the reconciler: consolidate or escalate.
A `decision` resets the counter to 0 and reopens the thread. Escalating sets
`status = 'escalated'` — that row is what the alert path and the dashboard read.

Enforced server-side deliberately: the reconciler is a persona, personas die
silently, and this is the guarantee that bounds spend. A dead reconciler must
degrade to "threads stop" and never to "threads loop."

**Thread participants are derived, not stored** (decided 2026-07-19). A
"conversation between Mau and Javiera" is just a thread they both posted in;
the viewer computes participants from `messages.from_agent` / `to_agent`. No
schema change, and a two-person thread renders as a P2P chat for free.
*Alternative if it stops being enough:* a `thread_participants` table, needed
only to **convene** a group — address three named personas before any of them
has spoken. Adding it later invalidates nothing derived.

**Counting rule:** every message increments. Chosen for being cheap.
*Alternative if it misfires:* count agent alternations instead, so a burst of
three messages from one persona counts as one round. More faithful to "is this
converging," slightly more code. Switch only when the cheap rule demonstrably
stalls a healthy thread.

---

## 2. API config — Go HTTP service

- **Bind:** Unix socket `/run/discuss.sock` (no port, local-user only) — or
  `127.0.0.1:9494` if you want curl-simplicity. Socket is the safer default.
- **Identity:** one bearer token per `(project, agent)`; the server derives
  `from_agent` and `project_id` **from the token**, never trusting a
  client-supplied `from`. This is what stops agent A posting as agent B.
- **Single writer:** the process owns the DB; all mutations serialize through it.

### Endpoints (all under `/projects/{pid}`)

| Method + path | Purpose | Body → Response |
|---|---|---|
| `POST /messages` | post / reply | `{to?, thread_id?, parent_id?, kind, subject?, body}` → `{id, thread_id, created_at}` |
| `GET /agents/{a}/inbox?unread=1&limit=N` | list | → `[{id, thread_id, from, kind, subject, body, created_at}]` |
| `GET /threads/{tid}` | full thread (tree) | → `{id, subject, status, messages:[…]}` |
| `POST /agents/{a}/ack` | mark handled | `{message_id?  \| thread_id?}` → `{acked}` |
| `POST /agents/{a}/drain` | **Stop-hook pickup** | `{session_id, stop_hook_active}` → `{block, reason?, delivered_ids}` |
| `GET /agents/{a}/wait?timeout_ms=55000` | **watcher long-poll** | → `{woke, count}` (returns on first undelivered msg or timeout) |
| `GET /healthz` | liveness | → `200` |

### `/drain` handler logic

```
if stop_hook_active:            return {block:false}     # already drained this cycle
if drains(session_id) >= MAX_DRAINS_PER_SESSION: return {block:false}
rows = undelivered(pid, agent)
if empty:                       return {block:false}
mark_delivered(rows, now); bump drains(session_id)
reason = REACTION_POLICY + format(rows)     # truncate to REASON_CAP, add inbox pointer if over
return {block:true, reason, delivered_ids: ids(rows)}
```

### `/wait` handler logic (long-poll + coalesce)

```
deadline = now + timeout_ms
loop:
  if undelivered(pid, agent) exists: return {woke:true, count:n}   # do NOT mark delivered here
  if now >= deadline:                return {woke:false, count:0}
  sleep(COALESCE_MS)                 # a burst within this window returns as one wake
```

`/wait` only *signals*; delivery still happens through `/drain` so the
delivered-cursor stays single-source. Enforce a per-agent **wake rate limit**
(token bucket, `WAKE_PER_MIN`) at this endpoint so a chatty pair can't wake in a
tight loop.

---

## 3. Hook config — per agent

Each agent session launches with its identity in the environment:

```
PROJECT_ID=alpha  AGENT_NAME=alfredo  DISCUSS_TOKEN=…  claude …
```

`settings.json` (project- or user-level). Three drain entry points + one watcher:

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [
        { "type": "command",
          "command": "discuss-hook drain",   "timeout": 5 },
        { "type": "command",
          "command": "discuss-hook watch",
          "async": true, "asyncRewake": true, "timeout": 600 }
      ] }
    ],
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "discuss-hook drain", "timeout": 5 } ] }
    ],
    "SessionStart": [
      { "matcher": "startup|resume",
        "hooks": [ { "type": "command", "command": "discuss-hook drain", "timeout": 5 } ] }
    ]
  }
}
```

Why three drain points: a stopped session's only re-entry is `UserPromptSubmit`
or `SessionStart`, so hooking those means a manual nudge or a restart also
picks up mail — the watcher (below) covers the fully-idle case.

### `discuss-hook drain`

```
read stdin JSON -> {session_id, hook_event_name, stop_hook_active}
if hook_event_name is empty: exit 0       # not a hook invocation — do NOT consume
resp = POST /projects/$PROJECT_ID/agents/$AGENT_NAME/drain
         {session_id, stop_hook_active}   with  --max-time 3
on any error/timeout:  exit 0            # FAIL-OPEN — never hang the agent
if not resp.block: exit 0
case hook_event_name of
  Stop | SubagentStop      -> print {"decision":"block","reason":resp.reason}
  UserPromptSubmit         -> print {"hookSpecificOutput":
                                {"hookEventName":"UserPromptSubmit",
                                 "additionalContext":resp.reason}}
  SessionStart             -> same, with hookEventName SessionStart
exit 0
```

**The output shape is not interchangeable across events.** `decision: "block"`
continues a stopping agent on `Stop`, but on `UserPromptSubmit` it *rejects the
prompt*. Sending the Stop shape to all three (as the first implementation did)
discards the wake, consumes the message, and leaves the session with no re-armed
watcher — deaf, with no error anywhere. `additionalContext` injects without
rejecting and is the correct shape for the other two.

**`drain` must refuse to run outside a hook.** Verified 2026-07-18: an agent read
its wake text, ran `discuss-hook drain` by hand, and only avoided eating its own
mail because empty stdin failed to parse. With `{}` on stdin the drain would
succeed and write the message to a stdout nothing is reading. Requiring
`hook_event_name` closes that.

Fail-open is mandatory: a dead API must degrade to "agent idles normally,"
never to "agent frozen."

### `discuss-hook watch`  (the asyncRewake watcher)

```
flock /run/discuss/watch-$PROJECT_ID-$AGENT_NAME.lock  (non-blocking)
  held?  exit 0                          # singleton per (project,agent)
loop:
  resp = GET /wait?timeout_ms=55000       with --max-time 60
  on error: sleep 2; retry (bounded, e.g. 5x) then exit 0
  if resp.woke:
     print to STDERR "<state the situation; name no command>"     # wake payload
     exit 2                               # <-- wakes the model; lock releases
  # timeout, nothing yet -> loop (still async/background, 0 token cost)
```

**How the wake actually arrives** (verified 2026-07-18, undocumented): `exit 2`
does *not* resume the stopped turn. Claude Code re-enters the session as a
**synthetic `UserPromptSubmit`** carrying the prompt
`<task-notification><summary>Stop hook feedback</summary></task-notification>`,
with the watcher's stderr attached as a `system-reminder`. Consequences:

- The **`UserPromptSubmit` drain is the one that services a wake**, not the Stop
  drain. Its output shape decides whether the wake lands.
- The stderr payload is delivered to the model **as an instruction it follows
  literally** — an early draft said "run `discuss-hook drain`" and the agent
  shelled out and did exactly that. State the situation; never name a command.

On exit 2 the agent wakes → the `UserPromptSubmit` drain injects the messages →
the agent acts → on completion Stop fires again (with `stop_hook_active`) → a
fresh `watch` re-arms (the previous one exited). The flock prevents watcher
pile-up when Stop fires repeatedly; observed working under a real double-Stop.

**Deploying the binary under a live cell:** install by writing beside the target
and renaming, never `cp` over it. `cp` rewrites in place and corrupts the
executable for every watcher currently running it, hanging all later
invocations — a silent cell-wide outage on redeploy.

> **SubagentStop note:** your agents are top-level `claude` processes, so `Stop`
> is correct. If any role is ever defined as a subagent, its Stop hook is
> auto-converted to `SubagentStop` — configure that event instead for it.

---

## 4. Config values (defaults)

| Key | Default | Notes |
|---|---|---|
| `bind` | `unix:/run/discuss.sock` | or `127.0.0.1:9494` |
| `db_path` | `/var/lib/discuss/discuss.db` | WAL |
| `WAIT_TIMEOUT_MS` | `55000` | client `--max-time 60` |
| `COALESCE_MS` | `750` | burst → one wake |
| `WAKE_PER_MIN` | `6` | per-agent token bucket on `/wait` |
| `MAX_DRAINS_PER_SESSION` | `8` | hard cost ceiling |
| `AGREEMENT_STALL` | `12` | messages since last `decision` → thread stalls |
| `REASON_CAP` | `10000` | hook output cap; truncate + pointer |
| `drain hook --max-time` | `3s` | fail-open on exceed |
| async `watch` timeout | `600s` | re-arm before this elapses |

---

## 5. Flows (recap)

**Busy agent** → finishes → `Stop` → `drain`: undelivered? block with them →
agent acts → finishes → `Stop` (stop_hook_active) → allow idle.

**Idle agent** → `watch` blocks on `/wait` (0 tokens) → peer `POST /messages` →
`/wait` unblocks (coalesced) → `watch` exit 2 → session re-enters as a synthetic
`UserPromptSubmit` → **that** drain injects the messages via `additionalContext`
→ agent acts → `Stop` (stop_hook_active) → `watch` re-arms.

**Stalling subject** → thread hits `AGREEMENT_STALL` messages with no
`decision` → server sets `status='stalled'` and rejects further posts → message
to the reconciler → they consolidate and post a `decision` (counter resets,
thread reopens) or set `status='escalated'` and stop. Escalated threads are the
human's queue.

Token spend happens **only** at: a `block:true` continuation, and an exit-2 wake,
plus whatever the woken/continued agent then does. Everything else is free.

---

## 6. Verification gate — PASSED 2026-07-18 (Claude Code v2.1.214, darwin/arm64)

The whole push-wake rests on `asyncRewake`, which is real but lightly
documented. Proved with ONE agent and a hand-posted message, against a
file-sentinel stand-in for `/wait` so the only variable was hook behaviour:

| # | Item | Result |
|---|---|---|
| 1 | `asyncRewake:true` + `exit 2` wakes a **fully stopped** session | **PASS** — 2m21s idle, woke unaided, 27ms from post to wake |
| 2 | stderr on exit 2 reaches the model as wake context | **PASS** — and is followed literally, see §3 |
| 3 | Re-arm after a wake, no pile-up, no gap | **PASS** — new watcher same millisecond; `lock_held` observed under a real double-Stop |
| 4 | `stop_hook_active` terminates the block loop | **PASS** |
| 5 | Async hook total-runtime cap vs `/wait` poll length | **OPEN** — no cutoff observed through 2m39s of heartbeats; needs a dedicated idle run before `WAIT_TIMEOUT_MS` is trusted |

The bet paid: §1 and §2 can be built against a wake mechanism known to work.

**What the gate cost to learn** — four defects, none of which announce
themselves at runtime, all detailed where they belong in §3: wrong output shape
per hook event (deafens the session), manual `drain` destroying a message,
wake text read as a command, and `cp`-over-a-running-binary hanging every
watcher. Assume the same class of failure in the API: this system's failure mode
is silence, not errors.

**Fallback if `asyncRewake` doesn't behave:** keep the drain hooks (Stop /
UserPromptSubmit / SessionStart) and wake idle agents by external injection —
a per-agent daemon that tails `/wait` and does `tmux send-keys` into the agent's
pane. Uglier and brittle, but bulletproof on billing and needs no undocumented
hook behavior. Falling back costs you the clean wake, not the whole design.
