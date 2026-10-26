---
waves:
  - wave: 1
    supervisor: sup-sample
    task: "facts-fields: one card, the 0042 fields"
    cards: [facts-fields]
    launched: 2026-10-20T10:00-03:00
    merged: 2026-10-22T11:30-03:00
    rounds:
      - card: facts-fields
        kind: build
        start: 2026-10-20T10:00-03:00
        end: 2026-10-20T10:40-03:00
        result: n/a
        reviewer:
        reason: "F1-F3 built"
      - card: facts-fields
        kind: review
        start: 2026-10-20T10:41-03:00
        end: 2026-10-20T10:55-03:00
        result: fail
        reviewer: ff-review
        cause: gate
        reason: "F2's gate asked for created_at; the spec was amended"
      - card: facts-fields
        kind: build
        start: 2026-10-22T10:00-03:00
        end: 2026-10-22T10:30-03:00
        result: n/a
        reviewer:
        cause: gate
        reason: "F2 rebuilt against the amended spec"
      - card: facts-fields
        kind: review
        start: 2026-10-22T10:31-03:00
        end: 2026-10-22T10:45-03:00
        result: pass
        reviewer: ff-review2
        reason: "pass, unmet none"
findings:
  - kind: environment
    to: hephaistos
    card: facts-fields
    what: "the sample host's clock ran 3 min behind"
  - kind: execution
    to: sample-fse
    what: "a stale comment in sample-repo/a.go"
---

# 2026-10-22 — sample run

A run record with waves, rounds with a cause, and findings (hestia 0042).
