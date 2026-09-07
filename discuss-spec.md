# `discuss` — implementation spec

Project-scoped threaded mailbox for a leaderless cell of Claude Code agents,
with drain-on-completion (Stop hook) and push-wake (asyncRewake watcher).
Terminal-session-per-agent; no central supervisor. Subscription-safe (local
service + first-party hooks only, never the metered API).

Three components: **DB** (§1), **Go API** (§2), **hooks** (§3). Config values in
§4, flows in §5, the verification gate in §6.

Kept in step with the implementation; last reconciled 2026-09-04. Where the
spec and `api/` disagree, the spec is wrong until someone fixes one of them.

---

## 1. DB config — SQLite, embedded in the API process

The API is the single writer; agents/hooks never touch the file directly.

```
PRAGMA journal_mode = WAL;      -- concurrent readers + one writer
PRAGMA busy_timeout = 5000;     -- wait out a held write lock
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;    -- WAL-safe, faster than FULL
```

Location: `~/.local/state/discuss/discuss.db` (`DISCUSS_STATE_DIR`, `0700`).
Nothing is ever deleted; `synchronous=NORMAL` survives an application crash,
not a power loss before checkpoint. No backup is taken — see
`docs/architecture.md`, *Where things live*.

### Schema versioning

Decided 2026-09-04. `Open` reads `PRAGMA user_version`, applies every step
above it in one transaction, and sets it. Steps are an ordered `[]string` in
`store.go` beside the `schema` constant; **that constant is step 1 and is
never edited again — every change is a new step.** A failing step fails
`Open`: the API refuses to start rather than run at the wrong version and
fail open into silence.

One test: open a file at version 0, assert it lands at the latest version
with every table present; open it again, assert nothing runs.

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
                                     -- pause|resume: server-written, see §7
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

-- the cell's control state (migration step 3, 2026-09-06; §7)
CREATE TABLE cells (
  project_id   TEXT PRIMARY KEY,
  paused_at    INTEGER,               -- NULL = running
  paused_by    TEXT,
  note         TEXT,
  pause_thread TEXT,                  -- the notice's thread; the resume replies into it
  pause_msg    TEXT,                  -- the notice's id: the drain/wait cut while paused
  clear_seq    INTEGER NOT NULL DEFAULT 0,  -- bumped by /clear; watchers compare, never reset
  updated_at   INTEGER NOT NULL
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

- **Bind:** `127.0.0.1:9494`, and nothing else. The agents' hooks and the
  browser view arrive on the same listener and are told apart by their token.
  The unix socket it replaced is gone (`docs/decisions.md`, 2026-09-04); a
  `DISCUSS_BIND` still naming `unix:` is refused at boot.
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
| `GET /agents/{a}/wait?timeout_ms=55000&seen_clear=N` | **watcher long-poll** | → `{woke, count, paused, clear_seq}` (returns on first undelivered msg, on `clear_seq > N`, or timeout) |
| `GET /threads?status=` | list by status, default `escalated` | → `{threads:[…]}` |
| `POST /threads/{tid}/status` | reopen · escalate · close | `{status}` → `{id, status}` |
| `GET /health` | roster liveness, live threads, deaf, undecided, the cell's control state | → `{agents:[…], threads:[…], push, cell, now}` |
| `POST /pause` | **human only** — stop the cell (§7) | `{note?}` → `{cell, thread_id, message_id}`; 409 if paused |
| `POST /resume` | **human only** — reopen it | `{note?}` → `{cell, thread_id, message_id}`; 409 if running |
| `POST /clear` | **human only** — every seat drops its conversation | → `{cell}`; 409 if running |
| `GET /metrics` | traffic: posted, addressed, taken, acked, `median_pickup_ms`; pairs; per day | → `{agents, pairs, days}` |
| `GET /search?q=&from=&to=&kind=&thread=` | archive, `LIKE` over subject and body | → `{messages:[…]}` |
| `GET /healthz` | liveness | → `200` |

### `/drain` handler logic

```
if stop_hook_active:            return {block:false}     # already drained this cycle
cell = cell_state(pid)
rows = undelivered(pid, agent, from = cell.pause_msg if cell.paused else none)   # §7: a pause holds the backlog
if empty:                       return {block:false}
if no row is kind pause|resume and drains(session_id) >= MAX_DRAINS_PER_SESSION:
                                return {block:false}     # the ceiling never blocks the human's stop
mark_delivered(rows, now); bump drains(session_id)
reason = REACTION_POLICY + format(rows)     # control notices first; truncate to REASON_CAP, add inbox pointer if over
return {block:true, reason, delivered_ids: ids(rows)}
```

### `/wait` handler logic (long-poll + coalesce)

```
deadline = now + timeout_ms
loop:
  cell = cell_state(pid)                                          # every tick: a pause or a clear lands mid-poll
  if seen_clear given and cell.clear_seq > seen_clear: return {woke:false, clear_seq}   # the watcher types /clear
  if undelivered(pid, agent, from = cell.pause_msg if paused) exists: return {woke:true, count:n, paused, clear_seq}
  if now >= deadline:                return {woke:false, count:0, paused, clear_seq}
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

### `discuss-hook watch --external`  (the pane watcher, 2026-09-06)

The same loop, outside the hook. Started by the seat's prelude
(`discuss-hook watch --external --parent $$ &`) so it lives as long as the
pane's shell, in the pane's own environment — `ZELLIJ_SESSION_NAME` and
`ZELLIJ_PANE_ID` name where to type.

| | hook mode | external mode |
|---|---|---|
| lock held | exit `lock_held` | wait, retry every 30s — the holder is a hook watcher with a timeout |
| runtime | `watchMaxRuntime`, above the hook's `timeout` | none; exits `parent_gone` when `--parent` is dead |
| API unreachable | exit after `watchMaxRetries` | back off to 60s, never exit |
| on news | wake text on stderr, `exit 2` | `zellij action write-chars --pane-id … <wake text>`, then `write 13`; keep polling |
| `clear_seq` moved (§7) | ignored — no pane | types `/clear` + Enter once per move; sends `seen_clear` on every poll after the first |
| after a wake | — | a grace (20s, doubling to 2m while undrained) before polling again — `/wait` answers "still undelivered" until the drain runs |

Precedence is by the lock: the external watcher starts before `claude`, so
every hook watcher a `Stop` arms exits `lock_held` at once. A seat in a plain
terminal has no pane; there the hook watcher remains the wake, unchanged.

After `DISCUSS_WAKE_BUDGET` consecutive wakes in which `/wait`'s undelivered
count never fell, the watcher stops typing, logs `reason=wake_budget` once and
keeps polling, re-arming when the count falls — a seat past
`MAX_DRAINS_PER_SESSION` reports the same backlog for the life of its session,
and the count falling is the only evidence out here that a wake reached a drain.

Gate:

| # | Item | Result |
|---|---|---|
| 1 | the prelude starts it; `hook.log` shows `mode=external … armed`; `/health` `alive` within 60s | **PASS** 2026-09-06 02:03 |
| 2 | queued mail is delivered with no human | **PASS** — three messages, `delivered=3`; in that run through the restart's `SessionStart` drain |
| 3 | a `Stop` hook watcher yields `lock_held`; the external pid keeps heartbeating | pending the seat's next `Stop` |
| 4 | a post to an idle seat is typed into its pane within 1s; the persona answers | **PASS** 2026-09-06 02:42 — typed 849ms after the post (750ms of it `/wait`'s coalesce), drained 607ms later, answered 44s after the post; one wake, `grace=20s` |
| 5 | `probe -k` the seat: the watcher exits `parent_gone`, no orphan | |
| 6 | the API down a minute: backoff, no exit, recovery | |
| 7 | a seat idle past 7h is still `alive` | in progress — Chino's external watcher passed 36m, already past the old ceiling; two more seats queued on the lock behind six-hour hook watchers |
| 8 | a plain-terminal seat is unchanged | by construction: `not_in_zellij` exits 0 |
| — | first run's defect: five wakes typed inside a second; fixed by the grace, pinned by `TestExternalDoesNotStormAnUndrainedSeat` | |

## 4. Config values (defaults)

| Key | Default | Notes |
|---|---|---|
| `DISCUSS_STATE_DIR` | `~/.local/state/discuss` | db, tokens, backups, logs |
| `DISCUSS_BIND` | `127.0.0.1:9494` | the only listener: agents and the page both |
| `DISCUSS_DB` | `<state>/discuss.db` | WAL |
| `DISCUSS_TOKENS` | `<state>/tokens.json` | loaded once at boot |
| `DISCUSS_UI_DIR` | *(unset — view off)* | a checkout of the `ui` repo, served at `/` |
| `DISCUSS_UI_AGENT` | *(required with `UI_DIR`)* | the identity the view reads as |
| `DISCUSS_UI_PROJECT` | *(required with `UI_DIR`)* | comma-separated; one token injected per cell, first is the default |
| `WAIT_TIMEOUT_MS` | `55000` | client `--max-time 60` |
| `COALESCE_MS` | `750` | burst → one wake |
| `WAKE_PER_MIN` | `6` | per-agent token bucket on `/wait` |
| `MAX_DRAINS_PER_SESSION` | `8` | hard cost ceiling |
| `DISCUSS_WAKE_BUDGET` | `5` | external watcher: consecutive wakes with no fall in the undelivered count before it stops typing |
| `AGREEMENT_STALL` | `12` | messages since last `decision` → thread stalls |
| `REASON_CAP` | `10000` | hook output cap; truncate + pointer |
| `DISCUSS_BODY_MAX` | `8192` | bytes of message body; a longer post is `413` — write the content to a file and post its path |
| `drain hook --max-time` | `3s` | fail-open on exceed |
| async `watch` timeout | `600s` | re-arm before this elapses |
| `projects.json` `human` | *(written by bootstrap.sh from cell.json)* | the one identity that may `/pause`, `/resume`, `/clear`; unnamed = nobody |

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


---

## 7. Pause, clear, resume — the human's stop (2026-09-06)

**Why.** `MAX_DRAINS_PER_SESSION` caps what a session may receive, and a seat
past it still has undelivered mail — so its watcher keeps waking it, and every
wake is a turn that delivers nothing. On 2026-09-06 all five camp seats reached
the ceiling on the same day: 1480 `drain ceiling reached` lines, one external
watcher typing a wake into its pane every 2m40s for ten hours. The only stop
that existed was `launchctl unload` of the API. This section is the stop that
keeps the API up, and the rotate that resets the ceiling.

**State.** One row per cell (`cells`, §1). `paused_at` set = paused. `clear_seq`
is a counter the human bumps and every external watcher compares against the
value it last saw.

**Three verbs, human only** — the identity `projects.json` names as the cell's
`human` (bootstrap.sh writes it from `cell.json`; unnamed means nobody may):

| Verb | Server does | Every seat gets |
|---|---|---|
| `POST /pause {note}` | posts a `pause` notice (broadcast, new thread, from the human) and records the pause in one transaction | the notice, alone — *stop, write your hand-off (bitácora, memory file), end your turn, stay silent until resumed* |
| `POST /clear` | bumps `clear_seq`; refused unless paused | its external watcher types `/clear` into its pane — a new session id, an empty context, the drain count reset |
| `POST /resume {note}` | posts a `resume` notice into the pause thread, lifts the pause, closes the thread | the notice first, then the backlog the pause held — *re-read agents/ and your bitácora, then continue* |

**While paused:**
- `POST /messages` from anyone but the human → `409 the cell is paused by X`.
- `/drain` and `/wait` see only messages with `id >= pause_msg`: the notice and
  what the human says. The backlog is held, so a paused seat is not woken for
  it — which is what ends the storm.
- A delivery that carries a `pause` or `resume` **bypasses the drain ceiling**.
  The ceiling caps what agents do to each other; the human's stop must reach
  the seat that has spent it.
- The notice text names files, never commands; a `pause` delivery replaces the
  "then continue" footer with "hand off, then stop"; a control notice is
  formatted before anything else in the same delivery.
- `/health` carries a top-level `cell` block: `{paused, since_seconds, by, note,
  thread, clear_seq}`. Rows are untouched (health-golden.json holds).

**The human's procedure** (`ops/README.md`, "Pause a cell"):

```
eval "$(discuss-api token env camp pablo | sed 's/ claude$//')"   # identity for the calls
discuss-hook pause -m "why"      # seats hand off and go quiet
discuss-hook cell                # watch: last drain ages grow, undelivered stays
discuss-hook clear               # optional: every pane types /clear
discuss-hook resume -m "what changed"
```

**Events:** `cell.paused`, `cell.resumed`, `cell.cleared` — `{by, note, thread,
clear_seq}` (factory-push-spec §2). The record ignores types it does not know.

**Boundaries:** a seat in a plain terminal has no pane, so `clear` does nothing
there; `/clear` is typed by hand. The hook-mode watcher ignores `clear_seq`.
A pause is not a thread status: threads stay as they were, and the stall guard
is untouched.

Gate:

| # | Item | Result |
|---|---|---|
| 1 | an agent's `/pause` → 403 naming the human; a cell with no `human` → 403 saying so | unit: `TestOnlyTheHumanControlsTheCell` |
| 2 | `pause`: the notice reaches a seat, the backlog before it does not, agent posts → 409, the human's posts go through | unit: `TestPauseSilencesTheCell` |
| 3 | a seat at the drain ceiling receives the pause | unit: `TestPauseReachesASeatAtTheDrainCeiling` |
| 4 | `clear` on a running cell → 409; on a paused one, a watcher polling with `seen_clear` returns within a coalesce tick and types `/clear` exactly once per move | unit: `TestClearSignalsTheWatchers`, `TestExternalClearsOnlyWhenTheCounterMoves` |
| 5 | `resume`: notice first, then the held backlog; posting works; the pause thread is closed | unit: `TestResumeReleasesTheBacklogBehindTheNotice` |
| 6 | live: the camp cell paused during the 2026-09-06 storm; wakes stop within one grace; every seat's `hook.log` shows the notice delivered | **PASS** 2026-09-06 12:30 — paused 300ms after the API came up; five `woke_seat count=1` and five `delivered=1` inside 16s, all through sessions at the ceiling (8 drains); one wake slipped into the 300ms race; no wake after 12:30:38 |
| 7 | live: `clear` on five zellij panes — five `cleared_seat` lines, five fresh session ids | pending Pablo's call |
| 8 | live: `resume` after a clear — each seat re-reads its files and continues; the backlog arrives behind the notice | pending |
