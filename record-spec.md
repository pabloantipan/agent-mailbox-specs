# `discuss-record` — implementation spec

The central side. Receives every factory's event stream, keeps it, projects
it into things worth reading, and answers two kinds of reader: a dashboard
that watches every factory, and agents that ask what the crew has decided.

Never on a hot path. Never wakes anyone. Can be down for a day and no cell
notices. The architecture is `docs/platform.md`; the sending side is
`factory-push-spec.md`.

Status: **spec, not built.** 2026-09-04. Name provisional.

---

## 1. Business rules

1. **`events` is append-only and is the truth.** Every other table is a
   projection and can be dropped and rebuilt from it.
2. **Ingest is idempotent** on `(factory, id)`. A duplicate is a `202` and a
   no-op.
3. **Ingest never fails a whole batch for one bad row.** The bad row is
   parked in `quarantine` with a reason and reported back; the rest land.
4. **Projections are applied in the ingest transaction.** At crew scale the
   cost is nothing, and the dashboard reads a consistent state. The only
   async work is what costs money: embeddings.
5. **A factory key pushes and registers for its own factory, and reads every
   factory** — masked beyond its own (§4a). A developer token reads every
   factory and issues or revokes keys. Nothing writes to a factory it is not.
6. **Unknown event types are stored and ignored.** A newer mailbox can emit
   before the record has learned the type; nothing is lost.
7. **The read API's health shape equals the mailbox's.** The same static page
   renders a local cell or a central factory with no branch.
   The record therefore answers the page's own paths — `/projects/{cell}/…`
   are aliases of the factory routes (§4). A missing field is degradation,
   rendered as absence (`view-spec.md` §8); a different route would be a fork
   the page cannot hide, and is refused.
8. **Personal data is stored raw and controlled at read.** PLV applies the Ley
   de Protección de Datos at visualization, not at saving. Every path that
   returns a body — read API, MCP, dashboard — passes through the `Redactor`
   (§4a). The database itself is never a visualization: IAM only, no
   developer with a `psql` prompt.
9. **Every body-returning read is audited.** Who, which factory and cell,
   which thread or query, when, and at what mask level — emitted in the shape
   PLV's audit sink already takes. In v1, not later.
10. **The record never calls out.** It computes and exposes state; something
    polls it. Alerting lives in Cloud Monitoring, not here.

## 2. Data model — Postgres 16 with pgvector

```sql
-- identity
CREATE TABLE factories (
  id          TEXT PRIMARY KEY,               -- hostname -s
  developer   TEXT NOT NULL,                  -- Firebase uid
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen   TIMESTAMPTZ
);
CREATE TABLE factory_keys (
  key_hash    TEXT PRIMARY KEY,               -- sha256; the key itself is never stored
  factory     TEXT NOT NULL REFERENCES factories(id),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at  TIMESTAMPTZ
);
CREATE TABLE cells (
  factory     TEXT NOT NULL REFERENCES factories(id),
  cell        TEXT NOT NULL,
  initiative  TEXT NOT NULL,
  title       TEXT,
  client      TEXT,
  push_since  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (factory, cell)
);

-- the truth
CREATE TABLE events (
  factory     TEXT NOT NULL,
  id          TEXT NOT NULL,                  -- the outbox ULID
  cell        TEXT NOT NULL,
  type        TEXT NOT NULL,
  v           INT  NOT NULL,
  at          TIMESTAMPTZ NOT NULL,           -- the mailbox's clock
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload     JSONB NOT NULL,
  PRIMARY KEY (factory, id)
);
CREATE INDEX idx_events_factory_at ON events(factory, at);
CREATE INDEX idx_events_cell_type  ON events(factory, cell, type, at);

CREATE TABLE quarantine (
  factory, id, received_at, reason TEXT, raw JSONB, PRIMARY KEY (factory, id)
);

-- projections: everything below is rebuildable from events
CREATE TABLE messages (
  factory, cell, id, thread, parent, from_agent, to_agent, kind, subject, body, at,
  PRIMARY KEY (factory, cell, id)
);
CREATE INDEX idx_messages_thread ON messages(factory, cell, thread, at);

CREATE TABLE threads (
  factory, cell, id, subject, status, since_decision INT, created_at, last_at,
  PRIMARY KEY (factory, cell, id)
);

CREATE TABLE seats (
  factory, cell, agent, last_wait_at, last_drain_at, undelivered INT,
  PRIMARY KEY (factory, cell, agent)
);

CREATE TABLE pickup_samples (
  factory, cell, agent, message, at, pickup_ms BIGINT
);
CREATE INDEX idx_pickup ON pickup_samples(factory, cell, agent, at);

CREATE TABLE decisions (
  factory, cell, message, thread, subject, body, at,
  embedding VECTOR(768),                      -- NULL until the worker fills it
  PRIMARY KEY (factory, cell, message)
);
CREATE INDEX idx_decisions_embedding ON decisions
  USING hnsw (embedding vector_cosine_ops);
```

Column types elided where obvious; the migration files are the authority.
`decisions` is `messages WHERE kind = 'decision'` kept as its own table
because it carries the vector and is the first thing anything searches.

**`decisions` is the crew's decision log.** Decided 2026-09-05: the `decision`
message is canonical and the cell's rulings file in git is the reconciler's
projection of it. A ruling that was never posted as a `decision` message is
not in this table, by design, and the record does not go looking for it.

## 3. How events project

| Event | Applies to |
|---|---|
| `message.posted` | insert `messages`; upsert `threads` (subject on first, `since_decision`, `last_at`); if `kind = decision` insert `decisions` with `embedding NULL` |
| `message.delivered` | insert `pickup_samples`; `seats.undelivered - 1` |
| `message.acked` | nothing yet — kept in `events` for when a reader wants it |
| `thread.status` | update `threads.status` |
| `wait.polled` | `seats.last_wait_at`; `factories.last_seen` |
| `wait.woken` | nothing beyond `last_seen` |
| `drain` | `seats.last_drain_at` |

Derived health (§4) is computed at read time from `seats` and `threads` with
the mailbox's own thresholds: stale at 150s without a poll, deaf when the
oldest undelivered is older than that. **The numbers are the mailbox's**, so
the two views never disagree about what stale means.

`discuss-record --role migrate rebuild` truncates every projection and
replays `events` in `(factory, id)` order. That command existing is what makes
rule 1 true rather than aspirational.

## 4. API contract

All under `/v1`. Errors are `{"error": "..."}`. Auth is `Authorization:
Bearer` — a factory key or a developer token; each row below says which.

```
POST /v1/factories/keys                          developer token
  Request:  { "factory": "lodestar" }
  Response: 201 { "key": "<shown once>", "factory": "lodestar" }
  Errors:   401; 409 if the factory belongs to another developer

POST /v1/factories/{factory}/keys/revoke         developer token
  Request:  { "key_hash": "..." }                   omit to revoke every key of the factory
  Response: 204

POST /v1/factories/{factory}/cells               factory key, must match {factory}; idempotent upsert
  Request:  { "cell": "camp", "initiative": "ccint-camp", "title": "…", "client": "…" }
  Response: 204                                   title and client are what the views show
  Errors:   403 (key is for another factory)

POST /v1/ingest                                  factory key
  Request:  { "factory": "lodestar", "events": [ envelope, ... ] }   ≤ 500
  Response: 202 { "accepted": 497, "rejected": [ { "id": "...", "reason": "unknown v 2" } ] }
  Errors:   400 (not a batch), 403 (factory mismatch), 413 (> 500),
            429 (per-factory token bucket, `Retry-After` set)

GET  /v1/factories                               either
  Response: { "factories": [ { "id", "developer", "last_seen", "cells": [ ... ] } ] }

GET  /v1/factories/{factory}/health              either
  Response: the mailbox's /health shape — { "agents": [...], "threads": [...] } —
            grouped per cell, plus "push": { "last_event_at", "lag_seconds" }
            as seen from this side. Rule 7.

GET  /v1/factories/{factory}/cells/{cell}/threads            either
GET  /v1/factories/{factory}/cells/{cell}/threads/{id}       either
  Response: the mailbox's shapes.

GET  /v1/decisions/search?q=&factory=&cell=&limit=           either
  Response: { "decisions": [ { "factory", "cell", "thread", "subject", "body", "at", "score" } ] }
  Hybrid: ILIKE on subject/body always; cosine on embedding when the query
  can be embedded and the row has one. Score is the vector distance when
  present, else 0.

GET  /v1/factories/{factory}/pickup?agent=&since=            either
  Response: { "samples": [ { "at", "pickup_ms" } ] }  — the series behind "degrading"

GET  /health     liveness, no auth, no DB
GET  /ready      readiness: SELECT 1
```

```
Aliases for the page                             either; read-only   (decided 2026-09-16)
GET  /projects/{cell}/health                     → that cell's group of /v1/factories/{factory}/health,
                                                   in the mailbox's own shape (rule 7; health-golden.json)
GET  /projects/{cell}/threads                    → /v1/factories/{factory}/cells/{cell}/threads
GET  /projects/{cell}/threads/{id}               → /v1/factories/{factory}/cells/{cell}/threads/{id}
  {factory} comes from the caller: a factory key names its own factory; a
  developer token resolves the cell across factories and answers 409 naming
  them when it is not unique. 404 for a cell nobody registered.
  Every other /projects/{cell}/... path answers 404 with one sentence: the
  record has no inbox, no drain, no post — it serves a reader, never a seat.
  The page never learns a second scheme; the record answers the page's own.
```

```
GET  /            the static page, from RECORD_UI_DIR — the same three files the
GET  /{file}      mailbox serves; no token injected here, the page's connect panel
                  takes a factory key once. Same origin, so there is no CORS on the
                  record, ever.                                     (decided 2026-09-05)
```

**Consumers of the read API**, so the shapes are designed against real
readers: the static `ui` (one factory, the mailbox's health shape — rule 7,
served by the record itself, above);
the **management app** (every factory, the flags, the admin endpoints —
`docs/platform.md`, named and not yet specified); and Cloud Monitoring
through `/metrics`. The organizer does not read the record: it is one factory
on one laptop and has the mailbox's own `/health` for that.

Admin endpoints — key revocation, retention, the deletion procedure — are
**not in v1's contract.** They are named here so their home is known, and
they are specified with the management app.

### 4a. The `Redactor` and the audit

Decided 2026-09-04. One function, on every body-returning path:

```go
// Redact applies PLV's masks to a body according to who is asking. Until PLV
// defines roles: raw for cells on the caller's own factory, masked otherwise.
func (r *Redactor) Redact(caller Caller, factory, cell, body string) (out string, level MaskLevel)
```

Masks are PLV's, copied not reinvented: RUT `12345678-9` → `1234****-9`,
email `user@example.com` → `u***@example.com`, values under keys named
`password`, `token`, `secret`, `apikey`, `authorization`, `credential`,
`*Token` dropped. The `(caller, cell) → MaskLevel` rule is one function so
PLV's roles slot in without touching a handler.

Every call to `Redact` emits one audit event — `record.read` with `caller`,
`factory`, `cell`, `thread` or `query`, `at`, `level` — as a structured log
line in PLV's audit shape, and to `RECORD_AUDIT_TOPIC` when set. Search
results audit once per result returned, not once per query.

**Named, not designed here:** deletion against `events` for a data-subject
request (a procedure with an owner: delete the events, `rebuild`), and
retention (`cells.retain_until`, applied by a job; the number is PLV's).

### 4b. State flags and metrics

Decided 2026-09-04. Three flags, computed at read time from the same
thresholds the metrics use, so the two never disagree:

| Flag | On | True when |
|---|---|---|
| `dark` | a factory row in `/v1/factories` | no event for `RECORD_DARK_AFTER` |
| `degrading` | a seat in `/{f}/health` | last hour's median pickup > `RECORD_DEGRADING_FLOOR`, or > `RECORD_DEGRADING_FACTOR` × its 24h median |
| `at_risk` | a thread in `/{f}/health` | `since_decision ≥ RECORD_AT_RISK_SINCE` and quiet > `RECORD_AT_RISK_QUIET` |

```
GET /metrics        Prometheus text format, no auth, scraped by the collector
  discuss_factory_last_event_age_seconds{factory}
  discuss_seat_pickup_ms{factory,cell,agent,quantile="0.5",window="1h"}
  discuss_thread_since_decision{factory,cell,thread}
  discuss_ingest_batches_total{factory,result}      accepted | rejected | error
```

`monitoring/` holds the alert policies as YAML, applied by Cloud Build with
the manifests. v1 ships three, one per flag, wired to PLV's existing
notification channel for this service. Thresholds in the policies are read
from the same values as the record's config; when one changes, both do.

## 5. MCP

Served by the same binary at `/mcp` (streamable HTTP), authenticated exactly
as the read API. Tools, v1:

| Tool | Input | Returns |
|---|---|---|
| `search_decisions` | `query`, `limit=10`, optional `factory`, `cell` | the search rows above, body included |
| `get_thread` | `factory`, `cell`, `thread` | the thread's messages |
| `factory_health` | optional `factory` | seats and live threads, all factories when omitted |

A persona reaches it through `.mcp.json` at the initiative root, and the
`discuss` skill tells it when to call `search_decisions`: before proposing
anything that a decision elsewhere might already cover.

## 6. The worker — embeddings

`discuss-record --role worker`. Polls `decisions WHERE embedding IS NULL`
every 10s, embeds `subject + body`, writes the vector. Behind an interface:

```go
type Embedder interface {
    Embed(ctx context.Context, text string) ([]float32, error)
}
```

`RECORD_EMBED=vertex` uses Vertex AI `text-embedding-005` (768 dims) via
workload identity; unset means the worker exits at boot and search stays
`ILIKE`. **It is the only component that costs money per event**, and it is
off until a reader needs it.

## 7. Layout — the PLV golden path

`record/` is the first repo in this initiative with a remote — on PLV's git
host, because Cloud Build triggers from it. `ops` stays local-only by hook;
this one gets the same `pre-commit` token guard installed on init, and
`working-on/initiative.yaml` gains a `ports_to` for it.

```
record/
├── cmd/discuss-record/main.go          --role api | worker | migrate [rebuild]
├── internal/
│   ├── handler/     ingest_handler.go  read_handler.go  mcp_handler.go
│   ├── service/     ingest_service.go  (dedup, quarantine, project)
│   │                read_service.go    (health derivation, search)
│   │                embed_service.go
│   ├── repository/  events_repo.go  projections_repo.go  decisions_repo.go
│   │                *_integration_test.go   //go:build integration, testcontainers
│   ├── model/       envelope.go  health.go  ...
│   └── auth/        factory key hashing, Firebase ID token verification
├── migrations/      goose: 0001_identity.sql 0002_events.sql 0003_projections.sql
├── k8s/             deploy-api.yaml  deploy-worker.yaml  job-migrate.yaml  service.yaml
├── monitoring/      dark.yaml  degrading.yaml  at-risk.yaml   (Cloud Monitoring policies)
├── Dockerfile       multi-stage, distroless static, nonroot
├── docker-compose.yml   pgvector/pgvector:pg16 + record --role api
├── cloudbuild.yaml
└── go.mod           pgx/v5, goose/v3, testcontainers-go, the MCP Go SDK
```

Interfaces are declared where they are consumed. No `any` at a boundary; the
envelope is a typed struct with a typed payload per event. Errors wrap with
`fmt.Errorf("ingesting %s/%s: %w", factory, id, err)`. Logs are `slog` JSON
with `labels.project = discuss-record` so they sit beside every other PLV
service in Cloud Logging.

## 8. Deployment

- **Cloud SQL** Postgres 16, `cloudsql.iam_authentication` on, pgvector
  extension enabled. Reached from GKE through the Auth Proxy sidecar under a
  workload-identity service account with `cloudsql.client`. No DB password
  exists anywhere. Point-in-time recovery on, seven-day window: laptops delete
  pushed rows after seven days, so from day eight the record is the only copy
  of every stream.
- **`deploy-api.yaml`**: 2 replicas, rolling update `maxUnavailable: 0`,
  `/health` liveness and `/ready` readiness, requests `250m/256Mi`.
- **`deploy-worker.yaml`**: 1 replica, same image, `--role worker`.
- **`job-migrate.yaml`**: runs `--role migrate` before a rollout; goose takes
  an advisory lock so two never race.
- **Cloud Build**: test → build → push to Artifact Registry → `gke-deploy`, in
  the pipeline shape every PLV service uses.
- **Locally**: `docker compose up` gives Postgres with pgvector and the API on
  `:8080`; `go test -tags=integration ./...` starts its own Postgres through
  testcontainers and needs nothing running.

Ingress is HTTPS at a hostname the factories are configured with
(`DISCUSS_PUSH_URL`). There is no unauthenticated route but `/health` and
`/ready`.

## 9. Edge cases

- **Events for a cell never registered.** Accepted into `events`; projections
  are applied; the cell shows under its factory with `initiative: null` until
  registration arrives. Rule 6 in spirit: never lose, never block.
- **A factory sends `at` in the future or far past.** Stored as sent;
  `received_at` sits beside it. Dashboards order by `received_at` for
  liveness and by `at` for the timeline.
- **Two factories claim the same id.** Different factories, different
  streams — `(factory, id)` is the key. Same factory, same id: dedup.
- **A key is used from two machines.** Allowed; the key names the factory,
  not the host. Rotating it is the remedy, and it is deliberate.
- **The embedder is down.** Vectors stay `NULL`, search stays `ILIKE`, the
  worker logs and retries. Nothing else notices.
- **A body contains personal data.** Expected: it is stored raw by
  instruction and masked at read (§4a). A data-subject deletion request is
  `DELETE FROM events WHERE factory = ? AND cell = ? [AND id = ?]` followed by
  `rebuild` — a procedure with an owner, not something the API offers.
- **A body contains a secret.** It should not: the laptop withholds it before
  the outbox row exists (`factory-push-spec.md` §5). If one arrives anyway,
  the same deletion procedure applies and the laptop's guard has a bug.

## 10. Config

| Key | Default | Notes |
|---|---|---|
| `RECORD_DB_URL` | *(required)* | `postgres://` via the proxy, or compose's |
| `RECORD_BIND` | `:8080` | |
| `RECORD_UI_DIR` | *(unset — no page)* | the `ui` repo's three files, served at `/` |
| `RECORD_FIREBASE_PROJECT` | *(required)* | verifies developer ID tokens for key issuance |
| `RECORD_EMBED` | *(unset — off)* | `vertex` |
| `RECORD_EMBED_MODEL` | `text-embedding-005` | 768 dims; changing it means re-embedding |
| `RECORD_INGEST_MAX` | `500` | events per batch |
| `RECORD_INGEST_RATE`, `_BURST` | `5`, `20` | batches per second per factory, and the burst |
| `RECORD_AUDIT_TOPIC` | *(unset — structured log only)* | Pub/Sub topic in PLV's audit sink shape |
| `RECORD_DARK_AFTER` | `10m` | ten missed 55s polls |
| `RECORD_DEGRADING_FLOOR`, `_FACTOR` | `30s`, `10` | absolute, and relative to the seat's own day |
| `RECORD_AT_RISK_SINCE`, `_QUIET` | `7`, `1h` | five short of the stall guard |

## 11. Verification gate

One factory, one cell, `docker compose` locally first, then the same against
Cloud SQL.

| # | Item | Result |
|---|---|---|
| 1 | a hand-built batch of the seven event types lands; every projection row matches by hand | |
| 2 | the same batch again: `202`, `accepted: 0`, no new rows | |
| 3 | a batch with one bad `v`: the other rows land, the bad one is in `quarantine` with its reason | |
| 4 | `--role migrate rebuild` after dropping `messages` reproduces it row for row | |
| 5 | `/v1/factories/{f}/health` renders in the static `ui` page with no code change, and both repos' health tests decode their own `/health` into `specs/health-golden.json` — rule 7's mechanism | |
| 6 | a `decision` posted on the laptop is returned by `search_decisions` from a Claude Code session within 5s | |
| 7 | with the worker on, the vector is filled and the same query ranks it first among ten decoys | |
| 8 | a factory key for factory A cannot push as B: `403` | |
| 9 | integration tests pass with nothing running (`go test -tags=integration ./...`) | |
| 10 | a body with a RUT reads raw through the owning factory's key and masked through another's; both reads appear in the audit with their level | |
| 11 | `search_decisions` over ten results emits ten audit events, one per body returned | |
| 12 | stop a factory's pusher: within `RECORD_DARK_AFTER` its row reads `dark: true`, `/metrics` shows the age, and the `dark.yaml` policy fires to the test channel | |

## 12. What "v1" is

Items 1–6, 8–12. The worker (item 7) is v1.1 — the outbox, the record and the
MCP read path prove themselves against `ILIKE` first, so the seam is
exercised end to end before the first thing that costs money is switched on.

## 13. Decided while building — 2026-09-05

Everything the sections above left open, decided in the `record/` repo and
recorded here so the spec stays the authority. Each line names the section it
refines. None of these are policy: the four policy blockers on the card
(roles beyond own/others, the retention number, deletion's owner, the ingress)
are still config and named procedures, unanswered.

**Data model (§2).** `seats` has no `undelivered` column. It is derived at
read from `messages` minus `pickup_samples`, because a broadcast cannot be
counted for a seat that first appears after the post, and a counter would
drift from a rebuild. `cells` gains `retain_until`, unread by any job, so
retention has a home before it has a number. `pickup_samples` gains a primary
key on `(factory, cell, agent, message)`, which makes replay idempotent.

**Projections (§3).** A `message.posted` also creates the poster's `seats` row
with both liveness columns NULL, which reads as `never` — the mailbox's own
word for a persona that was never started. Without it an agent that posts and
never polls is missing from the roster entirely, which reads as "fine".
`factories.last_seen` is bumped by any event in a batch, not only by
`wait.polled`: `dark` asks when we last heard anything, and receipt time is
the honest answer. It is set from the record's clock, not the mailbox's, so a
factory replaying old events still reads as alive. Rebuild does not touch it,
because it is a fact about receiving rather than a projection of content.

**Ingest (§4).** The `202` body carries `duplicates` beside `accepted` and
`rejected`. `accepted` counts newly stored events, so a replayed batch reads
`accepted: 0` (gate item 2) and `duplicates` says why rather than leaving it
to look like a silent drop. A row too malformed to carry an id is quarantined
under `unparsed-<sha256 prefix>` so the table keeps its primary key and a
resend dedups.

**Keys (§4).** Only the developer who owns a factory may revoke its keys; any
other developer gets `403`. A fourth role, `--role key --factory f
--developer uid`, issues a key straight against the database: local compose
and the first bootstrap on infra have no developer token yet, and database
access is the operator's credential in both. The key goes to stdout, never
through the logger.

**Read API (§4).** `/{f}/health` nests the mailbox's `agents` and `threads`
blocks under `cells[]`, each with `cell`, `initiative`, `title` and `client`,
and adds the `push` block. `initiative` is null for a cell whose registration
has not arrived (§9). The thread list serves `{id, subject, status,
since_decision, created_at, last_at, messages}` in snake_case — the mailbox
currently serialises Go field names on that one route (`ID`, `ProjectID`, …),
which is a bug on that side rather than a shape worth copying; its single
thread route is already snake_case and the record matches it exactly.
`/{f}/pickup` takes `since` as unix milliseconds, the clock every other
timestamp in these APIs uses, and defaults to the last 24 hours; it also takes
an optional `cell`, because an agent name is only unique within one.
`/decisions/search` defaults to 20 results and caps at 200 — in v1 each result
is also an audit event.

**The Redactor (§4a).** `Redact` takes a context and a ref — factory, cell,
the message id, and thread or query — rather than the bare
`(caller, factory, cell, body)` above: the audit event needs the context for
correlation and the ref to name which body was returned. `service.Mask` keeps
the pure shape and is exported, so what "masked" means is readable without a
request in hand. A developer token is masked everywhere, having no factory of
its own; `service.Level` is the one function PLV's roles slot into. Credential
values are dropped at the masked level only, exactly as §4a reads — the
laptop's emit-side guard is what keeps a secret out of a raw body. An unquoted
credential value runs to the end of the line rather than to the next space, or
`Authorization: Bearer abc123` would leak the half that matters.

**The audit (§4a).** One event per body returned, on every path: a thread of
eight messages is eight events, a search of ten results is ten. The envelope
is PLV's audit shape — `event_id`, `event_ts`, `schema_version`, `event_kind`,
`trace_id`, `request_id`, `tier`, `emitter_*` — around the fields §4a names.
The structured log line is unconditional and never carries the body it audits;
`RECORD_AUDIT_TOPIC` adds the Pub/Sub copy, and a configured topic that does
not exist fails at boot rather than leaving a silent hole.

**Metrics and flags (§4b).** `dark` and
`discuss_factory_last_event_age_seconds` both measure time since the last
event or, for a factory that has never pushed, since it was registered. A
factory registered a minute ago is not dark; the same one ten minutes later
is. The two must use one clock or the flag and the alert that fires on it
disagree. `discuss_ingest_batches_total` counts one per batch: `accepted`
whole, `rejected` when rows were quarantined, `error` when it did not land.
`degrading.yaml` alerts on the absolute floor only — the relative half (ten
times the seat's own day) is a ratio of two windows a threshold condition
cannot express, and it stays a flag in the API.

**Search (§4, §6).** Hybrid is read as: a row is a candidate if it matches
ILIKE **or** carries a vector, ordered by cosine similarity, score 0 for a row
without one. The union rather than an ILIKE-only filter is what makes gate
item 7 mean anything — a decision found by meaning and not by substring. An
embedder that fails does not fail the search: the vector half is dropped and
ILIKE still answers. An embedding of the wrong dimension is refused per row,
never truncated into the index. v1 wires no embedder, so search is ILIKE and
every score is 0; v1.1 is the Vertex client and nothing else.

**The worker (§6).** With `RECORD_EMBED` unset it exits with status 0, not an
error: the state is configured, not failed, and a non-zero exit is a crash
loop that reads as a broken deploy. The Deployment's replica count moves with
the same switch, so the worker is never running with nothing to do.

**The page and deployment (§4, §8).** The page is `GET /{$}` and `GET /{file}`,
one segment and no dotfiles, rather than a file server at `/` that would
shadow an unknown `/v1` path and answer it with HTML. `/mcp` is registered per
method, because a pattern taking every method conflicts with `GET /{file}`.
The Cloud SQL proxy is a native sidecar in all three workloads: the migrate
Job cannot complete with an ordinary one. `cloudbuild.yaml` renders `k8s/` and
`monitoring/` in a single `envsubst` pass, which is the actual mechanism
behind "the thresholds are read from the same values as the record's config".
There is deliberately no Ingress manifest, no retention job and no deletion
job: all three are the open policy questions, and a manifest would answer one
by accident.

**Rule 7's mechanism (§11 item 5).** `specs/health-golden.json` holds the
field lists for an agent row and a thread row, the two flags that exist only
on the record, and the allowed values for `watcher`, `kind` and `status`. Each
repo has a test that checks its own `/health` against it. `record/` carries a
byte-identical copy so its tests still run once it is cloned alone on PLV's
host, and one of its tests fails if the two ever differ.

## 14. Policy defaults — 2026-09-16

The four blockers on the record-service card, answered with defaults so v1
closes as a local record. Each is one config value or one named procedure;
PLV changes the number, not the code.

- **Retention: 90 days** from a cell's registration. `cells.retain_until` is
  set on registration; the job that applies it is v1.1 and reads
  `RECORD_RETAIN_DAYS` (default 90; unset means never).
- **Unmasked beyond own factory: nobody.** `service.Level` stays own-factory
  raw, every other reader masked. The role that changes it is PLV's to name.
- **Deletion: the factory's developer owns it**, by the named procedure —
  `DELETE FROM events …` then `--role migrate rebuild` — run by hand and
  visible in the record's own audit log. No endpoint.
- **Ingress: decided when plv-infra hosts it.** ClusterIP and no manifest
  until then; local compose needs none.
- **Git host: github.com/pabloantipan/agent-mailbox-record** until PLV names
  one; the module path follows the host when it moves.
