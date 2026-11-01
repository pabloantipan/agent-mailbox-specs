---
title: Decision 0017, an unquoted crossing whose digits look octal
status: ruled
raised: 2026-11-01
raised_by: sample-fse
owner: pablo
ruled: 2026-11-01
ruled_by: pablo
options: [a, b]
chosen: b
crossing: 0012
---

## Question
One line. `0012` resolves to the integer 10 in YAML 1.1 and in Go's yaml.v3
(a leading zero is octal there); the reading is the decimal whole number 12,
never 10 (decision.schema.json crossing).
