---
title: Every list field written as a list
status: now
repos: [sample-repo]
branch: feat/now-lists
updated: 2026-10-20
next: "Run the gate"
stage: build
seat: builder-a
depends_on: [shipped]
boundary: [sample-repo/a.go, sample-repo/b.go]
threads: [01M5AAAAAAAAAAAAAAAAAAAAAA, 01M5BBBBBBBBBBBBBBBBBBBBBB]
spec: "docs/specs/sample-spec.md#goals"
gate: "docs/specs/sample-spec.md#goals"
risk: low
---

## Goal
The list form of every list field.
