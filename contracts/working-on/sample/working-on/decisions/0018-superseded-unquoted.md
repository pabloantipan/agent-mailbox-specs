---
title: Decision 0018, superseded, its numbers written unquoted
status: superseded
raised: 2026-11-01
raised_by: sample-fse
owner: pablo
ruled: 2026-11-01
ruled_by: pablo
options: [a, b]
chosen: a
crossing: 0008
superseded_by: 0020
---

## Question
One line. Every record number here is unquoted, as the skills write them.
YAML 1.2 (Go yaml.v3) reads `0008` as the float 8.0 and `0020` as the octal
16; YAML 1.1 reads `0008` as the string "0008" and `0020` as 16. The reading
is the digits as written: crossing 8, superseded by record 0020
(expected.json accepted_reading.record_numbers).
