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
