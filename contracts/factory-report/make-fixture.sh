#!/usr/bin/env bash
# Builds the factory-report contract fixture (contracts/factory-report/README.md) in DIR:
#   DIR/init/   an initiative whose working-on/ is a git repo with dated commits: five cards a-e of
#               stage `build`, a, b, c, d moved to done/ at 01:00, 01:10, 04:00, 08:00 UTC on
#               2026-10-20; record 0001 (ruled, cards a b c f: f still to cut), 0002 (proposed,
#               the stage gate, owner pablo); d waits on `next: "pablo: rule 0002"`, e depends on d
#   DIR/state/  a factory state dir: away-log.jsonl with one autonomous window (00:00-06:00) and
#               Argos's pulse-history.jsonl (two seats every 5 min, 00:00-06:00; usage limit
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
printf -- '---\ntitle: one\nstatus: ruled\nstage: build\ncards: [a, b, c, f]\n---\n' > "$W/decisions/0001-one.md"
printf -- '---\ntitle: two\nstatus: proposed\nowner: pablo\ncards: []\n---\n' > "$W/decisions/0002-two.md"
g() { git -c user.name=t -c user.email=t@example.invalid -c commit.gpgsign=false -c init.defaultBranch=main -C "$W" "$@"; }
commit() {  # commit ISO_UTC MESSAGE
  GIT_AUTHOR_DATE="$1+00:00" GIT_COMMITTER_DATE="$1+00:00" g commit -q -m "$2"
}
g init -q -b main
g add -A && commit 2026-10-19T12:00:00 "init"
mv_done() { g mv "$1.md" "done/$1.md" && commit "$2" "chore(cards): $1 done"; }
mv_done a 2026-10-20T01:00:00
mv_done b 2026-10-20T01:10:00
mv_done c 2026-10-20T04:00:00
mv_done d 2026-10-20T08:00:00

# ---------- the factory state ----------
ST="$OUT/state"; mkdir -p "$ST"
S=$(ep 2026-10-20T00:00:00); U=$(ep 2026-10-20T06:00:00)
{
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
