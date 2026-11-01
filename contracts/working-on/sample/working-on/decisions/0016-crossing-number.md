---
title: Decision 0016, a cell record that names its crossing unquoted
status: ruled
raised: 2026-11-01
raised_by: sample-fse
owner: pablo
ruled: 2026-11-01
ruled_by: pablo
options: [a, b]
chosen: a
crossing: 0019
---

## Question
One line. Written as the themis and working-on skills say (`crossing: NNNN`,
unquoted). A YAML 1.2 reader resolves `0019` to the float 19.0 and a YAML 1.1
reader to the string "0019"; the reading is the whole number 19
(decision.schema.json crossing).
