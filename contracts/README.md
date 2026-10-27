# Contract samples

Every contract between two repos (an HTTP body, an event, a file format both
sides read) has one sample here, and the tests on both sides read it. Neither
side keeps its own copy of the shape. Ruled by Pablo on 2026-10-25
(`~/agent-slack/docs/decisions.md`, "every contract between two repos has one
shared sample both sides test"). The contracts were mapped in
`~/agent-slack/docs/contracts/factory-contract-map.md`; the IDs below (H10,
F2, ...) are that map's.

## What a sample is

A JSON file, or a small tree with an `expected.json`, holding:

- **valid cases**: what the writer may send and the reader must accept, with
  what the reader must make of each one;
- **must-refuse cases**: what the reader must reject or report, each with its
  reason;
- **limits**: sizes, counts, enums, defaults.

A sample names its readers and writers, so whoever changes it knows who has
to follow.

## The rule

1. **A contract change edits the sample first.** Then the side that did not
   follow fails its own build, instead of a laptop finding out in production.
2. **Both sides' tests read the sample**, by path, from the specs repo. The
   repos sit beside `specs/` under `~/agent-slack/`, so a test in
   `<repo>/internal/x/` reads `../../../specs/contracts/<file>`.
3. **A missing sample fails the test; it never skips.** A guard that skips
   when its file is absent is a guard that never ran (the map's H10 found one).
4. **A vendored copy** (for a repo built alone elsewhere) is allowed only with
   a test that fails when the copy and this file differ, on the layout where
   both exist.

## The contracts

| Map | Sample | Writer side, its test | Reader side, its test | State |
|---|---|---|---|---|
| H10 | `../health-golden.json` | mailbox: `api/internal/api/health_load_test.go` `TestHealthRowFieldsMatchGolden` (reads it). record: `record/internal/handler/health_golden_integration_test.go` `TestHealthMatchesTheGoldenShape` (reads its vendored `record/specs/health-golden.json`; `TestGoldenCopyMatchesTheInitiative` compares the copy with this file) | UI `ui/app.js`, Deltagos `organizer/internal/discuss/discuss.go`, `check.sh`: no test reads it yet | mailbox reads it; record's copy equals it today (see Gaps) |
| H11, L3 | `mailbox-threads.json` | mailbox: `api/internal/api/contract_test.go` `TestThreadListKeysMatchSample` | role-relay, hephaistos-mail.sh, discuss-threads, tidy-check (claudecode), Deltagos `internal/discuss/client.go`: no test reads it yet | pins today's untagged keys |
| F2, F4 | `working-on/` (`sample/` + `expected.json`) | the working-on skill (`~/claudecode/skills/working-on/SKILL.md`), which names this tree as the format's reference | record `record/internal/kb/workingon/golden_test.go` (reads it); Deltagos `organizer/internal/scan`: no test reads it yet | written; both readers differ from it (`expected.json` `readers_today`) |
| 0042 facts | `working-on/schemas/` (`decision`, `card`, `run-record`, `spec` `.schema.json`) + `working-on/expected.json` `facts` (the new fields' sample records, the fact payloads, the enums `ruled_mode` and `cause`, must-refuse cases) | the working-on, fse, supervise and spec-craft skills (Hephaistos; their edits follow) | record's laptop indexer, the facts ledger (hestia 0042's cards): no test reads it yet | written (hestia 0042, crossing agent-slack 0022); old records stay valid; record to follow |
| 0042 facts | `forecast-state.json` (fact kind `forecast_extension`) | `factory forecast extend` (`~/claudecode/scripts/factory/forecast.py`, Hephaistos): no test reads it yet | Argos (`forecast.py`); record's laptop indexer (hestia): no test reads it yet | written; record to follow |
| H12 | `facts-batch.json` (hestia 0042: one batch with a row of every fact kind, the answers, caps and refusals, and the rows it never refuses: past 2147483647, holding U+0000, or refused by Postgres as sent and kept raw; quarantined) | record's forwarder, the laptop's facts ledger past its own cursor (facts-extract, hestia's card): no test yet | record `internal/handler/facts_integration_test.go` `TestFactsBatchReplaysTheSample` (Hestia's facts ingest: ledger, `facts_history`, quarantine, projections; reads it) | written; record to follow (facts-ingest), the writer with facts-extract |
| H3 | `usage-batch.json` | Deltagos `internal/service/usagepost.go`; record forwarder `internal/forward/usage.go`: neither test reads it yet | record `internal/usage/usage_test.go`, `migration_integration_test.go`, `internal/handler/usage_integration_test.go` (read it) | written (record side); writers' tests to follow: organizer's FSE, hestia's FSE |
| H1 | `ingest-batch.json` | mailbox pusher `api/cmd/discuss-api/push.go` (`push_test.go` does not read it yet); record forwarder `internal/forward/forward.go`, `client.go` | record `internal/handler/ingest_integration_test.go`, `internal/model/envelope_test.go` (read it) | written (record side); the mailbox pusher's test is Hephaistos's; both senders to follow ruling 4's retry rule (see Gaps) |
| H5, H6, H7 | `auth.json` | Keycloak realm `record/deploy/hestia/keycloak/hestia-realm.json` and enrolment `record/internal/enroll` (`auth_contract_test.go`); `GET /v1/auth/verify` | record `internal/auth/auth_contract_test.go`, `internal/handler/auth_contract_test.go`, `factory_name_integration_test.go`, `cmd/discuss-record/key_role_test.go`; gateway `deploy/hestia/test/verify-stub` and `gw-test.sh` (all read it; the file lists every one) | written |
| H4 | `kb-snapshot.json` | record forwarder `internal/forward` (`kb_send_test.go`), `internal/kb/snapshot_codec_test.go` | record `internal/model/kb_snapshot_contract_test.go`, `internal/handler/kb_snapshot_contract_integration_test.go` (read it; record vendors `specs/contracts/kb-snapshot.json`, equal today) | written |
| L2 | `index-initiatives.json` | the factory installer (Hephaistos), the operator's hand: no test writes it yet | record `deploy/lodestar/install.sh`: `internal/contracts/indexinitiatives_test.go` `TestInstallReadsIndexInitiatives` (renders every case); record `deploy/lodestar/index.sh`: no test reads it yet | written; install.sh reads it |

Owners (decisions.md 2026-10-25): hestia's FSE for the record side, the
organizer's FSE for the Deltagos side, Hephaistos for the mailbox, the
working-on sample and skill, and this folder. Hestia 0042 adds: the
working-on schemas, their enums and `forecast-state.json` are Hephaistos's
(Q5); `facts-batch.json` (H12) is hestia's FSE's, in this folder.

## Gaps known today

- **The record's health golden guard can still skip.** The vendored copy
  equals `../health-golden.json` today, and `TestGoldenCopyMatchesTheInitiative`
  now also looks in `~/agent-slack/specs`, so it runs on lodestar; where
  neither path exists it skips unless `RECORD_SPECS_REQUIRED=1` (rule 3 says
  fail). hestia's card.
- **Retry on 429 and 503 (ruling 4).** `ingest-batch.json` now says it: a
  sender honours `Retry-After` whenever a 429 or 503 carries it; without it
  every 5xx, 503 included, goes on the doubling backoff. The record forwarder
  (`internal/forward/forward.go` `shedWait`) still waits 5 s on a 503 with no
  header (hestia's FSE); the mailbox pusher (`push.go`) still ignores
  `Retry-After` on a 503 (Hephaistos).
- **The thread list's keys** are Go field names (`ID`, `Subject`, ...)
  because `store.Thread` has no json tags. Tagging them is a separate change
  that edits `mailbox-threads.json` and every reader it lists together.
