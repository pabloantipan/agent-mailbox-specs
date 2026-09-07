# `factory push` — implementation spec

The laptop side of the seam between a factory and the record. Every state
change the mailbox makes becomes an event row in the same transaction; a
pusher sends them to the record by cursor. Nothing here touches a wake.

Lives in `api/` — it is a change to `discuss-api`, not a new binary. The
receiving side is `record-spec.md`. The architecture is `docs/platform.md`.

Status: **spec, not built.** 2026-09-04.

---

## 1. Business rules

1. An event is written **in the transaction that made it true**. There is no
   event for a change that did not commit, and no committed change without
   its event.
2. Events are written **only for cells that opt in** (`push: true` in
   `cell.json`). A cell that has not opted in produces no outbox rows at all.
3. The pusher **never blocks a handler**. It runs in its own goroutine; every
   error it meets is logged and retried, and none reaches an agent.
4. Delivery is **at-least-once**. The record deduplicates on the event id; the
   pusher may resend freely after any doubt.
5. The stream is **ordered per factory** by ULID. A batch is contiguous rows
   in id order; the pusher never skips ahead of an unpushed row.
6. A row the record **rejects** is marked pushed with a reason and never
   resent. The stream must advance past a bad row; the record parks it.
7. The push never carries a seat token. The factory key is the only credential
   that crosses the wire.

## 2. Event vocabulary

All emitted by `discuss-api`. The hook binary is unchanged — its own log lines
(`armed`, `heartbeat`, `exit reason=…`) stay local; what the API observes of
the watcher is the `/wait` call itself, which is enough.

| Type | Emitted by | Payload |
|---|---|---|
| `message.posted` | `POST /messages` | `id, thread, parent, from, to, kind, subject, body, since_decision, thread_status` |
| `message.delivered` | `POST /drain` (per message, per reader) | `message, agent, pickup_ms` |
| `message.acked` | `POST /ack` | `message, agent` |
| `thread.status` | `POST /threads/{id}/status`, and the server's own stall | `thread, status, by, since_decision` — `by` is the agent, or `server` |
| `wait.polled` | `GET /wait` on entry | `agent, timeout_ms` |
| `wait.woken` | `GET /wait` on exit with messages | `agent, count, waited_ms` |
| `drain` | `POST /drain` | `agent, session, delivered, drains_in_session` |
| `cell.paused` | `POST /pause` (discuss-spec §7) | `by, note, thread, clear_seq` |
| `cell.resumed` | `POST /resume` | `by, note, thread, clear_seq` |
| `cell.cleared` | `POST /clear` | `by, thread, clear_seq` |

`message.posted` carries the body. That is what makes central search and
vectors possible, and it is why push is opt-in per cell rather than on by
default.

## 3. Envelope

```json
{
  "id":      "01M1N893SRDNTAD58H8R031KBQ",
  "v":       1,
  "factory": "lodestar",
  "cell":    "camp",
  "type":    "message.posted",
  "at":      1788493467448,
  "payload": { "...": "per type, §2" }
}
```

- `id` is the outbox row's ULID, not the message's: a message yields one
  `posted` event but one `delivered` per reader.
- `v` is the envelope version. The record ignores unknown `type`s and unknown
  payload fields; it rejects an unknown `v`.
- `at` is the mailbox's clock in unix millis, the same one that stamps
  `created_at` — so pickup latency is one clock minus itself.
- `factory` is read from `~/.local/state/discuss/factory`, written once by
  `bootstrap.sh` with `hostname -s` — what `working-on`'s `machine` already
  is. A file, not the live hostname, so renaming the laptop does not orphan
  the factory's history.

## 4. Data model

Added as **migration step 2** (`discuss-spec.md` §1, *Schema versioning*),
never as an edit to the `schema` constant:

```sql
CREATE TABLE IF NOT EXISTS outbox (
  id         TEXT PRIMARY KEY,      -- ULID; time-sortable, so also the cursor
  project_id TEXT NOT NULL,
  type       TEXT NOT NULL,
  payload    TEXT NOT NULL,         -- the envelope's payload, JSON
  created_at INTEGER NOT NULL,      -- unix millis, the store clock
  pushed_at  INTEGER,
  rejected   TEXT                   -- reason, when the record refused it
);
CREATE INDEX IF NOT EXISTS idx_outbox_unpushed ON outbox(id) WHERE pushed_at IS NULL;
```

`cell.json` gains one optional field:

```json
{ "project": "camp", "push": true }
```

`bootstrap.sh` reads it and writes the cell's entry into
`~/.local/state/discuss/projects.json`:

```json
{ "camp": { "push": false }, "plv-auth": { "push": true } }
```

The API re-reads that file when its mtime changes, so opting a cell in is a
bootstrap run and never a restart — a restart drops every watcher. The API
knows nothing about `cell.json`; it knows this file. (Decided 2026-09-05; the
first draft had a boot-time env var, which is wrong for a thing that changes
at runtime.)

## 5. Emission

One function, called inside every transaction that changes state:

```go
// emit writes an event row in the caller's transaction. It is a no-op for a
// cell that has not opted in, so the boundary is enforced at the write.
func (s *Store) emit(ctx context.Context, tx *sql.Tx, project, typ string, payload any) error
```

Call sites: `Post` (posted, and `thread.status` when the stall trips),
`MarkDelivered` (one per message), `Ack`, `SetThreadStatus`, and — outside a
transaction, so wrapped in one — the `/wait` handler on entry and exit, and
`/drain` after a successful delivery.

`pickup_ms` on `message.delivered` is `delivered_at - created_at` computed in
`MarkDelivered`, where both are at hand.

### What never leaves

Decided 2026-09-04. Before `emit` writes a `message.posted` row it tests the
body with the same character-diversity heuristic the `ops` pre-commit hook
uses (moved into `internal/guard`, called by both). A body that trips it is
**withheld**: the event is still written, `body` replaced by
`[withheld: secret-shaped content]`, and one log line names the message id.
The thread's shape survives centrally; the secret does not exist in any
pushable form.

Personal data is **not** masked here. PLV applies the Ley de Protección de
Datos at visualization, not at saving; the guard for a RUT or an email is the
record's read-time `Redactor` (`record-spec.md` §4a). Nothing on the laptop
inspects a body for personal data.

## 6. The pusher

A goroutine started by `run()` in `cmd/discuss-api/main.go` when
`DISCUSS_PUSH_URL` is set. Otherwise the outbox is written (for opted-in
cells) and never drained — a factory that has not been pointed at a record
still keeps its stream for the day it is.

```
loop:
  wait for: the interval, or a nudge from emit (a channel with capacity 1)
  rows := SELECT * FROM outbox WHERE pushed_at IS NULL ORDER BY id LIMIT batch
  if none: continue
  resp := POST {url}/v1/ingest  Authorization: Bearer <factory key>
          body {"factory": f, "events": [envelopes]}
  case 202:
      UPDATE outbox SET pushed_at = now WHERE id IN accepted
      UPDATE outbox SET pushed_at = now, rejected = reason WHERE id IN rejected
      log any rejected, one line each
      backoff = 0
      if len(rows) == batch: loop immediately   -- draining a backlog
  case 401, 403:
      log; backoff = 5m                          -- a key problem is not transient
  case 5xx, network, timeout:
      log; backoff = min(backoff*2, 60s), starting at 1s
  case 429:
      log; backoff = Retry-After, else 5s          -- the record is shedding load, not refusing us
  case other 4xx:
      log the body; backoff = 5m                 -- we sent something malformed
```

- **Never marks anything pushed on a non-202.** A lost `202` means a resend,
  which the record deduplicates.
- **Request timeout 10s.** A slow record must not pin the pusher.
- The nudge means a post is on the wire within milliseconds when the record is
  reachable; the interval is the floor when it is not.

### What the local `/health` gains

```json
"push": { "enabled": true, "lag_seconds": 2, "unpushed": 3, "last_ok_at": 1788493469000, "last_error": "" }
```

`lag_seconds` is the age of the oldest unpushed row. The browser view shows
it in the alert bar past 30s. This is the local half of "a gap in the stream
is a finding": the record sees the gap; the laptop sees why.

## 7. Identity

The factory key is a file, `DISCUSS_PUSH_KEY_FILE`, default
`~/.local/state/discuss/push.key`, `0600`, beside `tokens.json`. It is
obtained once:

```
organizer factory-key            # signs the developer in (existing Firebase flow),
                                 # POST /v1/factories/keys, writes the file
```

`organizer factory-key --rotate` issues a new key, writes the file, then revokes
the old one — in that order, so the pusher is never without a key.

The API reads it at boot and on every `401` (so a rotation needs no restart).
It never appears in a log line, in `ops/`, or in git — the existing
`pre-commit` token guard already refuses anything shaped like it.

## 8. Registration

Before a factory's events mean anything, the record must know which cell
belongs to which initiative. `bootstrap.sh` knows — `cell.json` and
`working-on/initiative.yaml` sit in the same root — and so does the
organizer when it brings a crew up. Either registers; the record upserts on
`(factory, cell)`, so both may:

```
POST /v1/factories/{factory}/cells   { "cell": "camp", "initiative": "ccint-camp" }
```

Events for an unregistered cell are accepted and held; they attach when the
registration arrives. The mailbox never waits on this.

## 9. Edge cases

- **The record is down for a day.** Rows accumulate; `lag_seconds` grows; the
  view says so. On return the pusher drains at full batch rate, contiguous,
  in order. Nothing is lost, nothing is reordered.
- **Two `discuss-api` processes on one laptop.** Cannot happen after the
  socket → TCP change: the second fails to bind. Before it, the outbox has a
  single writer regardless because the store does.
- **A cell opts in after months of history.** The stream starts now. `push_since`
  is recorded centrally; history stays in the cell's git and local database.
- **A cell opts out.** `emit` stops writing; already-written rows still drain.
  To stop *that* too, delete the rows — a deliberate act, by hand.
- **Clock skew between laptops.** Irrelevant to ordering (per-factory streams)
  and to pickup latency (one clock). Visible only when comparing factories'
  `at` values, which the record does with its own `received_at` beside them.
- **The outbox grows forever.** Pushed rows older than `DISCUSS_PUSH_KEEP`
  (default 7d) are deleted by the pusher after each successful batch. Unpushed
  rows are never deleted.

## 10. Config

| Key | Default | Notes |
|---|---|---|
| `DISCUSS_PUSH_URL` | *(unset — pusher off)* | the record's base URL |
| `DISCUSS_PUSH_KEY_FILE` | `~/.local/state/discuss/push.key` | `0600`; re-read on `401` |
| `DISCUSS_PROJECTS_FILE` | `<state>/projects.json` | per-cell `push`; written by `bootstrap.sh`, re-read on mtime |
| `DISCUSS_FACTORY_FILE` | `<state>/factory` | written once; equals `working-on`'s `machine` |
| `DISCUSS_PUSH_INTERVAL_MS` | `1000` | floor between attempts |
| `DISCUSS_PUSH_BATCH` | `500` | rows per request |
| `DISCUSS_PUSH_KEEP` | `168h` | retention of pushed rows |

## 11. Verification gate

To pass before the record is trusted with a real factory. Same discipline as
`discuss-spec.md` §6: one factory, one cell, hand-posted messages.

Run 2026-09-05 against `docker compose up` in `record/`, factory `lodestar`,
cell `camp`.

| # | Item | Result |
|---|---|---|
| 1 | a post appears in the record within 3s of the `202` in the pusher log | **pass** — posted 15:31:48.609, pushed .638, 29ms; all five event types land |
| 2 | with the record stopped, ten posts accumulate; `lag_seconds` climbs; the view shows it | **pass** — lag 31→38→45s, retries 4s/8s/16s, alert bar renders the line and the reason |
| 3 | on restart all ten arrive, in order, once — `SELECT count(*)` centrally equals local | **pass** — 15→25 events, ten ids ascending, no duplicates |
| 4 | replaying a batch by hand yields `202` with zero new rows | **pass** — `{"accepted":0,"duplicates":5}`, 25→25 |
| 5 | a cell without `push: true` writes **zero** outbox rows across a full thread | **pass** — post, wait, drain, answer and status: 0 rows |
| 6 | a rejected row is marked, logged, and the next batch proceeds past it | **pass** — marked with its reason, logged, quarantined centrally, the row beside it landed |
| 7 | a `401` backs off five minutes and recovers after the key file is replaced, no restart | **pass** — 401 15:35:44, recovered 15:40:44 on the rotated key, same pid |
| 8 | wake latency in `hook.log` is unchanged with the pusher on | **pass** — median 764ms opted out, 786ms with the pusher pushing live (n=15 each); both are the 750ms coalesce window |
| 9 | a body containing a token-shaped string arrives centrally as the marker, the thread intact; a body containing a RUT arrives raw | **pass** — proven with the store half, `internal/guard` |

Item 8 is the one that protects the product. If it fails, the pusher has
found its way onto the hot path and the design is wrong, not the code.
