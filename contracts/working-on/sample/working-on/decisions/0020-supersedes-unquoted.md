---
title: Decision 0020, supersedes two records written unquoted
status: ruled
raised: 2026-11-01
raised_by: sample-fse
owner: pablo
ruled: 2026-11-01
ruled_by: pablo
options: [a, b]
chosen: a
crossing: 0010
supersedes: [0018, 0019]
---

## Question
One line. `supersedes: [0018, 0019]` resolves to the floats 18.0 and 19.0 in
YAML 1.2 and the strings "0018", "0019" in YAML 1.1; `crossing: 0010`
resolves to the octal 8 in both. The reading: supersedes records 0018 and
0019, crossing 10 (expected.json accepted_reading.record_numbers).
