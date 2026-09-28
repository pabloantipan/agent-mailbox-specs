# Spec: initiative topology for the dark factory

Status: as built, 2026-09-26.
Scope: one initiative inside one factory (one laptop). Several initiatives run
in parallel. Each one is an initiative root with its own `working-on/`.

This spec describes what runs on this machine today. Items marked
**not built** are recorded intentions. Sources are the `working-on` and `supervise`
skills, `docs/patterns.md`, `docs/workflow.md`, `docs/decisions.md`,
`specs/discuss-spec.md`, `ops/`, and the organizer (`~/organizer`).

---

## 1. Purpose

The factory runs work in three patterns. What separates them is **what the
coordination goes through**:

```
                 Pablo (human)
                      │  policy: "decide:" lines on cards; escalated threads
        ┌─────────────┼──────────────────────────┐
        ▼             ▼                          ▼
      PAIR        SUPERVISOR                    CELL
  Pablo + one    one session ──► builders    personas as peers;
    session       (subagents or probe)       the reconciler seat
        │             │   └──► reviewer      consolidates
        ▼             ▼                          ▼
  decisions.md,   CARDS + git + gates        THE MAILBOX
     specs        (artifacts)                (messages + wake)
```

To choose among them:
- If the call is the human's, pair.
- If the gate can be written before the work starts, supervise.
- If the gate is what you are looking for, convene a cell.

The pair's output (decisions, and specs with gates) is what the other two consume.

Design goals:

1. Policy stays with Pablo. No agent answers a `decide:` line.
2. Every session is disposable. What survives lives in files and git.
3. Concurrency is bounded by **disjoint file boundaries**.
4. An idle agent costs nothing. It is woken from outside, on mail, never on a
   schedule.

---

## 2. Core principle: disposable sessions over durable artifacts

| Memory | Writers | Readers | Holds |
|---|---|---|---|
| **Specs + `docs/decisions.md`** | Pablo and the pair session; a cell's `decision` messages | everyone | intent, gates (verification tables), rulings |
| **Cards** (`working-on/<slug>.md`) | Pablo; the supervisor (creates them); the builder (Done, `next`); the reviewer (`review:`, `## Review`, move to `done/`) | everyone; the organizer, read-only | status, next action, build fields |
| **`progress.md`** | the builder | the continuation builder; the reviewer reads only the phase table and "Found, not asked" | phase table, choices made where the spec left room |
| **The mailbox** (`~/.local/state/discuss/discuss.db`, SQLite WAL) | cell seats through the API; the server writes the pause/resume notices | cell seats, the browser view, the pusher | threads, messages, decisions, delivery state |
| **Statusline records** (`~/.local/share/organizer/sessions/`) | `organizer statusline`, after every turn | `organizer agents`, `organizer runs` | per-process model, context fill, cost |
| **Run records** (`docs/runs/`) | the supervisor, by hand | the next supervisor, Pablo | one row per session: card, model, wall, context at finish, cost, verdict |

Invariants:

- **I1.** Any session can be killed without losing what matters. Builders
  write `progress.md` and the card before they stop.
- **I2.** A fresh supervisor is rebuilt from cards, specs, decisions and
  `progress.md` files. Nothing durable lives in its context.
- **I3.** Each card field has one writer. Only the reviewer writes `review:` and moves a
  card to `done/`. The builder never does either.
- **I4.** Cards stay cheap. There are four statuses, and a rule that makes a card
  expensive is wrong.

---

## 3. Roles

### 3.1 Pablo and the pair session

- Pablo decides policy, scope and gates. One session proposes and records.
- The output is a `docs/decisions.md` entry (decision, cost, rejected options) and
  spec sections that carry a verification table.
- The failure it guards against is a decision made in chat and never written down.

### 3.2 Supervisor

There is one supervisor per wave, and a human starts it (the `supervise` skill). It
decomposes, isolates, instructs, launches, monitors, intervenes, sequences
and retires. **It does not build and it does not review.**

It refuses to launch unless:

1. every card has a spec with a **gate** the builder can run
2. file boundaries are **disjoint** and named in the prompt
3. the card is the shared state and names its builder in `seat:`
4. commits are atomic and conventional, with no `Co-Authored-By`
5. dependencies are known (`depends_on`)
6. a monitor exists (`organizer agents`)
7. intervention keeps the conversation (`probe -r`)
8. every builder knows the 65% cap and the `progress.md` hand-off
9. a reviewer that is not the builder is available

The supervisor must not:
- Answer a `decide:` line. Put it on the card for Pablo.
- Read transcripts instead of cards.
- Write inside a builder's boundary.
- Review its own wave.

It is exempt from the 65% cap. Its mitigation is I2.

### 3.3 Builder

Each card gets one builder. Its prompt comes from `templates/task-prompt.md` and
has four mandatory headings: **Objective, Output, Sources, Boundaries**. The
prompt file is `~/.local/share/organizer/prompts/<initiative>-<card>.md`.

- Works only inside its boundary. When it shares a repo with another builder it
  works in a worktree (`<root>/.wt/<repo>-<task>`).
- Updates the card when `next` changes. At the gate it writes the last Done
  bullet and sets `next: "review: <branch, gate state>"`.
- Never sets `status: done`, never writes `review:`, never moves the card.
- At 65% context it stops at the next phase boundary, writes `progress.md`,
  updates the card, and ends the turn.
- Records what it found that the spec didn't anticipate under the card's Notes.
- Ends with a reply of at most 300 words: paths and SHAs, no diffs.
- Runs on Opus, which the prelude sets through `ANTHROPIC_MODEL`.

### 3.4 Reviewer

There is one reviewer per card. It starts after the builder sets `next: "review: …"`,
and its prompt comes from `templates/review-prompt.md`.

- It reads only the card, the spec sections, the gate table and
  `git diff main..<branch>`, and it runs the gate's checks. It never reads the
  builder's transcript or pane.
- It edits only `review:`, `## Review` and `updated`.
  - On a **pass** it sets `status: done` and runs `git mv` to move the card to `done/`.
  - On a **fail** it rewrites `next` to the first unmet gate item.
- A gate item it cannot verify from the diff is unmet. If the gate itself is wrong,
  it reports that to Pablo and doesn't rewrite the gate.

### 3.5 The cell and its reconciler (deliberation)

A cell is a set of persona seats defined in `ops/cells/<project>.json`
(`project`, `human`, `reconciler`, `workdir`, `push`, `model`). The seats are
leaderless peers, and any seat may open a thread. The **reconciler** is one of
those seats. Its duty is to consolidate threads into a `decision`, and it has no
authority to assign work.

### 3.6 The organizer (control plane)

The organizer is a desktop app plus a CLI (`~/.local/bin/organizer`). It is
**read-only over cards**. The only state it owns is the manual priority order.

| Command | Does |
|---|---|
| `status`, `board` | scan every initiative's cards and enrich them with git |
| `run <initiative> <card>` | refuse unless the card has `spec`, `gate` and `boundary`, and the prompt file exists; otherwise open the probe launch in an iTerm tab (`--print` only shows it) |
| `runs` | per-card sessions from the statusline records: wall, context, cost |
| `agents` | every agent process, grouped by initiative: live/working, context fill |
| `crew` | bring a cell's seats up as probe panes; show their health |
| `retire <init> --retirable [--run]` | plan or execute retiring seats no open card names |
| `clean` | sweep exited sessions |
| `statusline` | the Claude Code `statusLine` command (§8.4) |
| `sync` | push the per-machine snapshot to Firestore |
| `factory-key` | issue or rotate this laptop's key on the record |

It spawns sessions only when a human asks (`run`, `crew`, the app).

### 3.7 The record (cross-factory)

The record is `discuss-record`, which runs on PLV infra with Postgres and pgvector. It receives
every cell's events from the laptop's pusher, keyed by a factory key. It
computes state and serves the status page. Agents read it through MCP. Push is opt-in
per cell (`push: true`).

---

## 4. Specs and decisions (intent)

### 4.1 Layout

Specs live in the initiative's repos. In agent-slack:

```
specs/<name>-spec.md     # one spec per component; sections carry gate tables
docs/decisions.md        # append-only: decision, cost, rejected, dated heading
docs/patterns.md         # which pattern answers which question
docs/workflow.md         # the patterns drawn
docs/runs/<date>-<wave>.md, docs/runs/TEMPLATE.md
```

Cards point into specs by section: `spec: <path>#<section>` and
`gate: <path>#<section>`.

### 4.2 Rules

- Git history is a spec's version.
- A decision is a dated `## ` heading in `decisions.md` that records its cost and the
  options it rejected. A cell's `decision` message is canonical, and the rulings
  file is its projection.
- A card body links to a decision and never absorbs it.
- When a spec changes, Pablo or the supervisor edits the affected open cards.
  Cards in `done/` are never touched. Rework means a new card.

### 4.3 Flush points

| Trigger | What happens |
|---|---|
| a builder reaches 65% context | it writes `progress.md` and the card and ends its turn. The supervisor rotates it: `probe -k`, then a fresh session from `templates/continue-prompt.md` |
| a builder reaches its gate | it writes the last Done bullet and sets `next: "review: …"` |
| a session starts in an initiative | the `working-on-session` hook prints the initiative and its open cards (§8.3) |
| a cell is paused | seats write their hand-offs before `clear` (§8.2) |

---

## 5. Cards (execution memory)

### 5.1 Schema

```yaml
---
title: <one line>
status: now | next | blocked | done
repos: [<repo-dir>, ...]
branch: <main branch of the work>
updated: <YYYY-MM-DD>
next: "<one imperative line>"          # mandatory on every open card
# optional
due: <YYYY-MM-DD>                      # only a real date
start: <YYYY-MM-DD>
threads: [<discuss thread id>]
depends_on: [<card-slug>]
boundary: [<path>]                     # disjoint across a wave
spec: <path>#<section>
gate: <path>#<section>
seat: <cell seat>                      # the builder; retire keeps a seat an open card names
review: pass | fail                    # reviewer only
---
## Goal / ## Where / ## Done (≤5) / ## Next (≤3) / ## Blockers / ## Notes / ## Review
```

A card that has `spec`, `gate` and `boundary` can be launched. Without them it is a
note with a next action. A card stays under forty lines. Execution state is
the `next` line plus git.

### 5.2 State machine

```
next ──work starts──► now ──builder at gate──► now, next: "review: …"
                       │                            │
                       │ blocker (named owner)       ├─ pass ─► done  (reviewer; git mv to done/)
                       ▼                            └─ fail ─► now, next = first unmet gate item
                    blocked ──blocker cleared──► now
```

- A card in `done/` is history and is never edited.
- More than three `now` cards is a signal to talk to Pablo.
- The supervisor reads `depends_on` to sequence waves.

---

## 6. Concurrency

- **C1. One card, one builder.** A card that needs two builders is two
  cards.
- **C2. Disjoint boundaries.** A wave has as many builders as there are disjoint
  boundaries. Cards that share a file go in different waves. Cards that share only a
  repo get a worktree each.
- **C3. Waves by dependency.** The next wave launches when its trigger lands,
  usually a merge to `main`. Its prompt starts by rebasing.
- **C4. Resume by artifact.** A builder that died or was rotated is replaced by a
  fresh session. The new session reads `progress.md` and the card, and trusts the commits.
- **C5. Retirement by card.** A seat is retirable when three things hold:
  it is not the human or the reconciler, no open card names it in `seat:`, and it isn't working.

---

## 7. Mailbox contract (cells)

The mailbox is `discuss-api`. It is written in Go over SQLite, listens on `127.0.0.1:9494`, and is kept alive
by launchd (`com.pabloantipan.discuss`). A second launchd job
(`com.pabloantipan.discuss-backup`) runs `ops/backup.sh`. The supervisor pattern
does not use the mailbox: builders coordinate through artifacts only.

### 7.1 Identity and addressing

- There is one bearer token per `(project, seat)`, stored in
  `~/.local/state/discuss/tokens.json`. The server derives `from_agent` and
  `project_id` from the token, never from the body.
- The scope is a project (a cell). A message goes to one seat (`to_agent`) or is
  broadcast to the cell (`to_agent` NULL).
- Delivery is tracked per seat (`delivered_at`, ack).
- `projects.json` names the cell's `human`, the one identity allowed to pause,
  resume or clear it.

### 7.2 Message kinds (closed set)

| Kind | Meaning |
|---|---|
| `msg`, `question`, `answer`, `status` | conversation |
| `claim`, `yield` | advisory ownership of a subject |
| `done` | a seat's piece is finished |
| `decision` | canonical outcome of a thread; the only kind accepted on a stalled thread |
| `pause`, `resume` | control; only the server writes them, on the human's verbs |

The server rejects an unknown kind. Bodies are capped at 8192 bytes, and a longer
body gets a 413. Messages carry **references, not payloads**: paths, SHAs and card ids.

### 7.3 Endpoints (under `/projects/{pid}` unless noted)

| Endpoint | Caller | Purpose |
|---|---|---|
| `GET /healthz` (root) | organizer, ops scripts | liveness |
| `POST /messages` | seats, organizer | post or reply |
| `GET /agents/{a}/inbox` | seats | list messages |
| `POST /agents/{a}/ack` | seats | mark handled |
| `POST /agents/{a}/drain` | `discuss-hook drain`; organizer `PickUp` | deliver undelivered mail, count the drain against the session |
| `GET /agents/{a}/wait` | `discuss-hook watch` (both modes) | long-poll up to 55 s; signals only, never delivers |
| `GET /threads`, `GET /threads/{tid}` | seats, organizer, view | list by status (default `escalated`); full thread |
| `POST /threads/{tid}/status` | seats, organizer, `retire` | reopen, escalate or close |
| `GET /health` | organizer (`crew`, `agents`), view | roster liveness, live threads, the cell's control state, push status |
| `GET /metrics`, `GET /search` | view, organizer | traffic; archive search |
| `POST /pause`, `/resume`, `/clear` | the human's token only | the human's stop (§8.2) |
| `GET /`, `GET /{file}` (root) | browser | the view, when `DISCUSS_UI_DIR` is set |

### 7.4 Guards

| Guard | Value | Enforced by |
|---|---|---|
| Agreement stall | 12 messages since the last `decision` stalls the thread | server, on post |
| Drain ceiling | 8 drains per session; the pause and resume notices bypass it | `/drain` |
| Wake rate | 6 per minute per seat; bursts coalesce over 750 ms | `/wait` |
| Wake budget | 5 wakes with no fall in the undelivered count, then stop typing | external watcher |
| Reason cap | 10 000 characters of hook output, then truncate with a pointer to the inbox | `/drain` |
| Fail open | any API error or timeout: the hook exits 0 and the agent idles normally | every hook |
| Human authority | a cell with no `human` can't be paused by anyone (fails closed) | server |

---

## 8. How hooks and APIs interact

```
 ┌──────────────────────────── a Claude Code session ────────────────────────────┐
 │                                                                               │
 │  SessionStart ──► working-on-session ───────────► reads working-on/*.md       │
 │               ──► cbm-session-reminder                                        │
 │               ──► discuss-hook drain  ─┐  (cell workdirs only)                │
 │  UserPromptSubmit ► discuss-hook drain ─┤                                     │
 │  Stop ──────────► discuss-hook drain  ─┼──► POST /drain ─┐                    │
 │        └────────► discuss-hook watch ──┼──► GET  /wait  ─┤  (asyncRewake)     │
 │  after each turn ► organizer statusline ───► sessions/<pid>.json              │
 │  PreToolUse Grep|Glob ► cbm-code-discovery-gate                               │
 │  SubagentStart ► cbm-subagent-reminder                                        │
 └───────────────────────────────────────────────────────────┬───────────────────┘
                                                             │
  pane shell (probe) ── prelude: token env, model,            │
       discuss-hook watch --external ──► GET /wait ──────────┤
            └─ on news: zellij write-chars "<wake>" + Enter   │
                                                             ▼
                                   ┌──────────── discuss-api :9494 ────────────┐
   organizer ──/healthz, /health──►│ handlers ─► store (SQLite, one writer)    │
             ──/drain, /threads──► │               │ same tx                    │
   browser view ──/, /health────►  │               ▼                            │
   human token ──/pause /clear──►  │            outbox ─► pusher ──HTTPS──────► │──► discuss-record
                                   └────────────────────────────────────────────┘     (factory key)
   organizer ──IssueKey / RegisterCell──────────────────────────────────────────────► discuss-record
```

### 8.1 Wiring

| Where | What is installed | By |
|---|---|---|
| `~/.claude/settings.json` (user, every session) | `SessionStart` (startup, resume, clear, compact) → `cbm-session-reminder`, `working-on-session`; `PreToolUse` `Grep\|Glob` → `cbm-code-discovery-gate`; `SubagentStart` → `cbm-subagent-reminder`; `statusLine` → `organizer statusline` | by hand |
| `<cell workdir>/.claude/settings.json` (cell seats only) | `Stop` → `discuss-hook drain` (5 s) + `discuss-hook watch` (async, `asyncRewake`, 600 s); `UserPromptSubmit` → `drain`; `SessionStart` startup/resume → `drain` | `ops/bootstrap.sh`, copied from `ops/hooks/settings.json` |
| a seat's pane | prelude `~/.local/share/organizer/crew/<initiative>/<seat>.sh`: exports `PROJECT_ID`, `AGENT_NAME`, `DISCUSS_TOKEN` (from `discuss-api token env`), `AGENT_SESSION`, `ANTHROPIC_MODEL`; starts `discuss-hook watch --external --parent $$` | `organizer crew` |
| a builder's pane | prelude `~/.local/share/organizer/prompts/opus.prelude.sh` (model only; no discuss identity) | the supervisor, `organizer run` |

Builders and supervisors run outside cell workdirs and without tokens, so the
discuss hooks never fire for them. Of the hooks, they receive only the user-level
set.

### 8.2 Mail to an idle seat (the wake)

1. A seat or the organizer calls `POST /messages`. The server derives the sender from the
   token, writes the message, and writes an outbox row **in the same transaction**.
2. The seat's watcher is already blocked in `GET /wait`. Within 750 ms (the coalesce window)
   `/wait` returns `{woke:true, count}`. `/wait` never delivers.
3. The wake reaches the session one of two ways:
   - **External watcher** (a seat in a zellij pane). It holds the lock
     `watch-<project>-<seat>.lock`, types the wake sentence into its pane and
     presses Enter, then waits a grace period (20 s, doubling up to 2 min while the seat is undrained)
     and polls again. It never exits while the pane lives.
   - **Hook watcher** (a seat in a plain terminal). It prints the wake sentence
     to stderr and exits 2. Claude Code re-enters the session as a synthetic
     `UserPromptSubmit`, with the stderr attached as a system reminder.
4. Either way, the next event is a `UserPromptSubmit`. Its `drain` hook calls
   `POST /drain`, which marks the rows delivered, counts one drain against the
   session, and returns the messages. The hook injects them as
   `additionalContext`.
5. The agent acts. On `Stop`, `drain` runs again: if more mail arrived it returns
   `decision: block` with the messages, and the turn continues. Otherwise the
   stop proceeds, and the `Stop` hook arms a new hook watcher. If an external
   watcher already holds the lock, the new watcher exits at once with `lock_held`.

Output shape matters. `decision: block` is valid only on `Stop`, because on
`UserPromptSubmit` it rejects the prompt. `drain` refuses to run without a
`hook_event_name`, so an agent that shells out to it cannot eat its own mail.

### 8.3 Session start in an initiative

1. `working-on-session` walks up from the cwd to find `working-on/initiative.yaml`,
   then prints the initiative and its `now`, `next` and `blocked` cards with their `next` lines.
   It runs on startup, resume, clear and compact.
2. In a cell workdir, the `SessionStart` `drain` also delivers any mail that
   arrived while the seat was down.

### 8.4 Monitoring (statusline → organizer)

After every turn, Claude Code runs `organizer statusline` with its statusline
JSON. The organizer writes one record per agent process to
`~/.local/share/organizer/sessions/<pid>.json`, recording model, context fill and cost,
plus the identity from the environment. The organizer uses these records in three places:

- `organizer agents` joins them with the process table (sampled every 10 s)
  and with `GET /health` to show live, working and deaf seats.
- `organizer runs` archives them per card, matched by initiative and branch,
  before pruning.
- `organizer crew` checks `/healthz`. Its client defaults to `127.0.0.1:9494`
  and honours `DISCUSS_BIND`.

### 8.5 The human's stop

These three verbs are called with the human's token:

- **`POST /pause`** writes a `pause` broadcast. From then on `/drain` and `/wait` cut the
  backlog at the notice's id, so old mail is held and no seat is woken for it.
  The notice itself is delivered past the drain ceiling.
- **`POST /clear`** (only while paused) bumps `clear_seq`. Each external watcher
  sees it in `/wait` and types `/clear` into its pane. The seat gets a new session id,
  an empty context and a drain count of zero. `working-on-session` fires on `clear`.
- **`POST /resume`** writes a `resume` broadcast. It tells a seat with no memory
  who it is and what to read, and the held mail flows behind it.

### 8.6 Push to the record

The pusher runs inside `discuss-api` and reads the outbox by cursor. For a cell with
`push: true`, it POSTs versioned envelopes to the record with the factory key
(`push.key`). The backoff grows from 1 s to 1 min when the record is unreachable, and is 5 min for key
errors and malformed bodies. Push status appears in `GET /health`. Seat tokens
never leave the laptop.

The organizer's record client is write-only. It issues or revokes this laptop's
factory key and registers each cell it brings up.

### 8.7 Retirement touches both sides

`organizer retire <init> --retirable --run` does the following:
1. Kills the retirable seats' probe sessions. The conversations stay resumable.
2. Closes the wave's threads (`POST /threads/{tid}/status`) as the human.
3. Revokes the seats' tokens and restarts the mailbox.
4. Deletes their preludes and prompts, and rewrites and commits `cell.json`.

---

## 9. Lifecycle

### 9.1 Initiative start
1. Bootstrap `working-on/` (`initiative.yaml`, `done/`) and add the `## Initiative`
   section to the root `CLAUDE.md`.
2. Pablo and a pair session write spec sections with gates, plus the decisions.
3. Write cards with `spec`, `gate`, `boundary`, `depends_on` and `seat`.
4. Only if deliberation is needed: write `ops/cells/<project>.json`, run
   `ops/bootstrap.sh` (issues tokens, installs the hooks, writes `projects.json`), then
   run `organizer crew`.

### 9.2 A wave
1. **Decompose**: one card per builder, plus the dependency edges between cards.
2. **Prompt**: one file per builder, with the four headings.
3. **Isolate**: worktrees under `<root>/.wt/`.
4. **Launch**, by one of:
   - **subagents of the supervising session** (the standard)
   - **probe panes**, through `organizer run` or
     `PROBE_PRELUDE_FILE=… PROBE_PROMPT_FILE=… <family>-probe <name> <dir>` in an
     iTerm tab (session name ≤ 22 characters)
   - **Claude Code agent teams**, which need `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`
     and are off on this machine
5. **Monitor**: `organizer agents`, then the card, then the commits.
6. **Intervene**: `probe -r` to resume; send a message, then Enter; rotate at 65%.
7. **Review**: one reviewer per card.
8. **Retire and record**: `organizer retire … --retirable --run`, then write the run
   record.

### 9.3 Recovery
| Failure | Recovery |
|---|---|
| Builder crash or context past 65% | fresh session from `continue-prompt.md` reads `progress.md` and the card |
| Supervisor crash | a new supervisor reads cards, git and the run record. Subagent builders died with it, so relaunch them from their cards |
| Reviewer crash | relaunch; it holds no state |
| Seat storm or runaway cell | Pablo: `pause` → `clear` → `resume` |
| Mailbox down | hooks fail open; watchers back off (external: up to 60 s, never exit); launchd restarts the API; cards and git are untouched |
| Record unreachable | the outbox holds events; the pusher backs off and resumes from its cursor |

---

## 10. Guardrails

- **Context**: 65% cap per agent, shown by the organizer. The supervisor is the exception.
- **Cost per session**: drain ceiling (8), wake rate (6/min) and wake budget (5)
  for cells.
- **Launch gate**: `organizer run` refuses a card without `spec`, `gate` and
  `boundary`, or without a prompt file.
- **Model**: builders run on Opus, set by the prelude. Cell seats run on `cell.json`'s model,
  falling back to the organizer's `CrewModel`, then `opus`.
- **Permissions**: no session writes another session's permission settings.
- **Verification**: no card reaches `done/` without a recorded reviewer
  verdict.
- **Measurement**: a wave without a run record did not happen.
- **Deploying**: install binaries by writing beside the target and renaming.
  Running `cp` over a live `discuss-hook` corrupts every running watcher.

---

## 11. Open items

- [ ] Launch standard: subagents, probe or agent teams. To decide it, score one agent-team wave
      on the run-record columns.
- [ ] Build cell on the mailbox: builders post `done` and the supervisor is woken.
      **Not built.**
- [ ] organizer: a card landing in `done/` unblocks its `depends_on` dependents; context bar amber at 50%
      and red at 65%; cycle-time and rework columns in `runs`; a lint over a wave's
      cards. **Not built.**
- [ ] probe: `PROBE_MODEL`, `PROBE_PERMISSION_MODE`, `PROBE_RESUME_PROMPT`.
      **Not built.**
- [ ] The drain ceiling and the external watcher interact badly for a seat nobody pauses,
      because `/wait` doesn't know the session's drain count.
- [ ] A gate-running hook (`TaskCompleted`, exit 2). Deferred because it checks
      that a table is filled, not that the diff fills it.
