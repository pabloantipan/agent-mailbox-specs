#!/usr/bin/env bash
# Builds the factory-report contract fixture (contracts/factory-report/README.md) in DIR:
#   DIR/init/   an initiative whose working-on/ is a git repo with dated commits: five cards a-e of
#               stage `build`, a, b, c, d moved to done/ at 01:00, 01:10, 04:00, 08:00 UTC on
#               2026-10-20; record 0001 (ruled, cards a b c f: f still to cut), 0002 (proposed,
#               the stage gate, owner pablo); d waits on `next: "pablo: rule 0002"`, e depends on d.
#               Rulings: 0001 ruled `pablo` at init (2026-10-19 12:00, before every window; ruled_at from
#               git); 0003 proposed at init, ruled `pablo (away mode)` at 02:30 (ruled_at from git,
#               the commit that set status: ruled); 0004 added ruled at 07:15 by `operator` with
#               `ruled_at: 2026-10-20T07:10:00+00:00` (ruled_at from the field)
#               Run records (organizer 0168, the waves block; frontmatter `waves:` as hestia 0042's
#               run-record schema): runs/2026-10-20-sup1.md, wave 1 of sup1 (cards a, c; a failed
#               review, cause execution) and wave 1 of sup2 (cards b, e; launched 2026-10-19, one
#               round before every window, a failed review with no cause, e's build still open;
#               two of its rounds written as flow maps `- {card: b, ...}`, as many real records do);
#               docs/runs/2026-10-15-sup0.md (wave 1 of sup0, card z, outside the window);
#               docs/runs/TEMPLATE.md (skipped). d is in no wave.
#   DIR/state/  a factory state dir: away-log.jsonl with three autonomous windows (2026-10-01
#               01:00-03:00, ended by until; 2026-10-18 22:00 to 2026-10-19 02:00, ended by
#               `autonomous mode off`, no card moved; 2026-10-20 00:00-06:00, the last) and Argos's
#               pulse-history.jsonl (two seats every 5 min, 00:00-06:00 on 2026-10-20; usage limit
#               02:00-03:00; no row 05:05-05:25, a 30-min gap)
#   DIR/env.sh  the environment the sample was drawn in (source it before `factory report`)
# Deterministic: fixed author, committer and dates, so the commit hashes in expected.json hold.
# Writer: ~/claudecode/scripts/factory/test/report.sh. Readers: Deltagos (organizer lf57).
#
#   make-fixture.sh DIR        (DIR must not exist or be empty)
set -eu
[ $# -eq 1 ] || { echo "usage: make-fixture.sh DIR" >&2; exit 2; }
mkdir -p "$1"
OUT=$(cd "$1" && pwd -P)
[ -z "$(ls -A "$OUT")" ] || { echo "make-fixture.sh: $OUT is not empty" >&2; exit 2; }
export TZ=UTC GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
ep() { python3 -c "import datetime,sys;print(int(datetime.datetime.fromisoformat(sys.argv[1]+'+00:00').timestamp()))" "$1"; }

# ---------- the initiative ----------
I="$OUT/init"; W="$I/working-on"
mkdir -p "$W/done" "$W/decisions"
printf 'id: fixture\nstatus: active\n' > "$W/initiative.yaml"
cat > "$W/roadmap.yaml" <<'EOF'
stages:
  - id: base
    title: The base
    outcome: there is a base
    done: 2026-10-01      # ruled in 0000
  - id: build
    title: The build
    exit:
      - text: everything built
    gates: ["0002"]
EOF
card() {  # card SLUG [EXTRA FRONTMATTER]
  printf -- '---\ntitle: %s\nstage: build\nstatus: next\n%s---\n\nbody\n' "$1" "${2:-}" > "$W/$1.md"
}
card a; card b; card c
card d 'next: "pablo: rule 0002"
'
card e 'depends_on: [d]
'
printf -- '---\ntitle: one\nstatus: ruled\nruled_by: pablo\nstage: build\ncards: [a, b, c, f]\n---\n' > "$W/decisions/0001-one.md"
printf -- '---\ntitle: two\nstatus: proposed\nowner: pablo\ncards: []\n---\n' > "$W/decisions/0002-two.md"
printf -- '---\ntitle: three\nstatus: proposed\nowner: pablo\n---\n' > "$W/decisions/0003-three.md"
g() { git -c user.name=t -c user.email=t@example.invalid -c commit.gpgsign=false -c init.defaultBranch=main -C "$W" "$@"; }
commit() {  # commit ISO_UTC MESSAGE
  GIT_AUTHOR_DATE="$1+00:00" GIT_COMMITTER_DATE="$1+00:00" g commit -q -m "$2"
}
g init -q -b main
g add -A && commit 2026-10-19T12:00:00 "init"
mv_done() { g mv "$1.md" "done/$1.md" && commit "$2" "chore(cards): $1 done"; }
mv_done a 2026-10-20T01:00:00
mv_done b 2026-10-20T01:10:00
printf -- '---\ntitle: three\nstatus: ruled\nowner: pablo\nruled_by: pablo (away mode)\n---\n' > "$W/decisions/0003-three.md"
g add -A && commit 2026-10-20T02:30:00 "docs(decisions): 0003 ruled"
mv_done c 2026-10-20T04:00:00
printf -- '---\ntitle: four\nstatus: ruled\nruled_by: operator\nruled_at: 2026-10-20T07:10:00+00:00\n---\n' > "$W/decisions/0004-four.md"
g add -A && commit 2026-10-20T07:15:00 "docs(decisions): 0004 ruled"
mv_done d 2026-10-20T08:00:00

# ---------- the run records (not in working-on/'s repo: runs/ is unversioned) ----------
mkdir -p "$I/runs" "$I/docs/runs"
cat > "$I/runs/2026-10-20-sup1.md" <<'EOF'
---
waves:
  - wave: 1
    supervisor: sup1
    task: "a and c"
    cards: [a, c]
    launched: 2026-10-19T21:05-03:00
    merged: 2026-10-20T04:05+00:00
    rounds:
      - card: a
        kind: build            # build | review | ui-review | take
        start: 2026-10-20T00:10+00:00
        end: 2026-10-20T00:40+00:00
        result: n/a
        reviewer:
        reason: "built"
      - card: a
        kind: review
        start: 2026-10-20T00:41+00:00
        end: 2026-10-20T00:45+00:00
        result: fail
        reviewer: a-rev
        cause: execution
        reason: "A2 unmet"
      - card: a
        kind: build
        start: 2026-10-20T00:46+00:00
        end: 2026-10-20T00:55+00:00
        result: n/a
        reviewer:
        cause: execution
        reason: "A2 fixed"
      - card: a
        kind: review
        start: 2026-10-20T00:56+00:00
        end: 2026-10-20T00:59+00:00
        result: pass
        reviewer: a-rev
        reason: "unmet none"
      - card: c
        kind: build
        start: 2026-10-20T03:05+00:00
        end: 2026-10-20T03:40+00:00
        result: n/a
        reviewer:
        reason: "built"
      - card: c
        kind: review
        start: 2026-10-20T03:41+00:00
        end: 2026-10-20T03:55+00:00
        result: pass
        reviewer: c-rev
        reason: "unmet none"
  - wave: 1
    supervisor: sup2
    task: "b and e"
    cards: [b, e]
    launched: 2026-10-19T20:00+00:00
    merged:
    rounds:
      - card: b
        kind: build
        start: 2026-10-19T20:00+00:00
        end: 2026-10-19T20:50+00:00
        result: n/a
        reviewer:
        reason: "built, before the window"
      - {card: b, kind: review, start: 2026-10-20T00:20+00:00, end: 2026-10-20T00:30+00:00, result: fail, reviewer: b-rev, reason: "B1 unmet, no cause written"}
      - {card: b, kind: build, start: 2026-10-20T00:31+00:00, end: 2026-10-20T01:00+00:00, result: n/a, reviewer: , reason: "B1 fixed (flow map, as 22 organizer records write rounds)"}
      - card: b
        kind: review
        start: 2026-10-20T01:02+00:00
        end: 2026-10-20T01:08+00:00
        result: pass
        reviewer: b-rev
        reason: "unmet none"
      - card: e
        kind: build
        start: 2026-10-20T05:40+00:00
        end:
        result: n/a
        reviewer:
        reason: "in progress"
---

# 2026-10-20 - sample run (sup1, sup2)
EOF
cat > "$I/docs/runs/2026-10-15-sup0.md" <<'EOF'
---
waves:
  - wave: 1
    supervisor: sup0
    cards: [z]
    launched: 2026-10-15T10:00+00:00
    merged: 2026-10-15T12:00+00:00
    rounds:
      - card: z
        kind: build
        start: 2026-10-15T10:00+00:00
        end: 2026-10-15T11:00+00:00
        result: n/a
---
EOF
cat > "$I/docs/runs/TEMPLATE.md" <<'EOF'
---
waves:
  - wave: 1
    supervisor: <sup<n>>
    cards: [<card-slug>, a]
---
EOF

# ---------- the factory state ----------
ST="$OUT/state"; mkdir -p "$ST"
S=$(ep 2026-10-20T00:00:00); U=$(ep 2026-10-20T06:00:00)
S0=$(ep 2026-10-01T01:00:00); U0=$(ep 2026-10-01T03:00:00)
S1=$(ep 2026-10-18T22:00:00); U1=$(ep 2026-10-19T02:00:00)
{
  printf '{"t": %s, "kind": "switch", "who": "pablo", "text": "autonomous mode on until 03:00"}\n' "$S0"
  printf '{"t": %s, "kind": "switch", "who": "pablo", "text": "autonomous mode ended at 03:00 (until passed)", "since": %s, "until": %s}\n' "$((U0 + 60))" "$S0" "$U0"
  printf '{"t": %s, "kind": "switch", "who": "pablo", "text": "autonomous mode on"}\n' "$S1"
  printf '{"t": %s, "kind": "switch", "who": "pablo", "text": "autonomous mode off"}\n' "$U1"
  printf '{"t": %s, "kind": "switch", "who": "pablo", "text": "autonomous mode on until 06:00"}\n' "$S"
  printf '{"t": %s, "kind": "switch", "who": "pablo", "text": "autonomous mode ended at 06:00 (until passed)", "since": %s, "until": %s}\n' "$((U + 60))" "$S" "$U"
} > "$ST/away-log.jsonl"
python3 - "$ST/pulse-history.jsonl" "$S" <<'EOF'
import json, sys
path, s = sys.argv[1], int(sys.argv[2])
with open(path, "w") as f:
    for k in range(0, 73):                  # every 5 min, 00:00 to 06:00
        t = s + k * 300
        if 5 * 3600 < t - s < 5 * 3600 + 1800:  # no row from 05:05 to 05:25: a 30-min gap
            continue
        st = "usage_limit" if 2 * 3600 <= t - s <= 3 * 3600 else "idle"
        for seat in ("fixture-probe-fse", "fixture-probe-b1"):
            f.write(json.dumps({"t": t, "seat": seat, "initiative": "fixture", "state": st}) + "\n")
EOF

# ---------- the environment ----------
cat > "$OUT/env.sh" <<EOF
export TZ=UTC
export FACTORY_STATE_DIR="$ST"
export FACTORY_NOW=$(ep 2026-10-21T00:00:00)
export FACTORY_OPERATOR=pablo
unset FACTORY_REPORT_ENVS FACTORY_REPORT_OUTAGE_MIN ORGANIZER_CONFIG
EOF
