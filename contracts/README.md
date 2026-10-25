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
| H10 | `../health-golden.json` | mailbox: `api/internal/api/health_load_test.go` `TestHealthRowFieldsMatchGolden` (reads it). record: `record/internal/handler/health_golden_integration_test.go` (reads its vendored `record/specs/health-golden.json`) | UI `ui/app.js`, Deltagos `organizer/internal/discuss/discuss.go`, `check.sh`: no test reads it yet | mailbox reads it; record copy is behind (see Gaps) |
| H11, L3 | `mailbox-threads.json` | mailbox: `api/internal/api/contract_test.go` `TestThreadListKeysMatchSample` | role-relay, hephaistos-mail.sh, discuss-threads, tidy-check (claudecode), Deltagos `internal/discuss/client.go`: no test reads it yet | pins today's untagged keys |
| F2, F4 | `working-on/` (`sample/` + `expected.json`) | the working-on skill (`~/claudecode/skills/working-on/SKILL.md`), which names this tree as the format's reference | record `record/internal/kb/workingon` (hestia's card), Deltagos `organizer/internal/scan` (organizer's card) | sample written; both readers differ from it (`expected.json` `readers_today`) |
| H3 | `usage-batch.json` | Deltagos `internal/service/usagepost_test.go` | record `internal/usage`, `internal/handler/usage_integration_test.go` | not written: hestia's and organizer's FSEs |
| H1 | `ingest-batch.json` | mailbox pusher `cmd/discuss-api/push_test.go`; record forwarder | record `internal/handler/ingest_integration_test.go` | not written: hestia's FSE, mailbox side Hephaistos |
| H5, H6, H7 | `auth.json` | Keycloak realm, `enroll` | record `internal/auth`, gateway `gw-test.sh` | not written: hestia's FSE |
| H4 | `kb-snapshot.json` | record forwarder | record `internal/handler/kb_snapshot_integration_test.go` | not written: hestia's FSE |

Owners (decisions.md 2026-10-25): hestia's FSE for the record side, the
organizer's FSE for the Deltagos side, Hephaistos for the mailbox, the
working-on sample and skill, and this folder.

## Gaps known today

- **The record's health golden copy is behind.** `mailbox_only` (thread
  `undecided`, agent `deaf_reason`) was added here on 2026-10-25; the
  record's vendored copy has not been refreshed, and its guard
  `TestGoldenCopyMatchesTheInitiative` looks at `../../../specs/` from
  `record/internal/handler`, which on lodestar is `~/hestia-monorepo/specs`
  (absent), so it skips. hestia's card.
- **The thread list's keys** are Go field names (`ID`, `Subject`, ...)
  because `store.Thread` has no json tags. Tagging them is a separate change
  that edits `mailbox-threads.json` and every reader it lists together.
