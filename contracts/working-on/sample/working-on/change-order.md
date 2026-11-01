---
title: A card that names what it changes in the platform model
status: next
repos: [sample-repo]
branch: feat/change-order
updated: 2026-10-31
next: "Launch once now-lists is merged"
depends_on: [now-lists]
boundary: [sample-repo/c.go]
spec: "docs/specs/sample-spec.md#goals"
gate: "docs/specs/sample-spec.md#goals"
risk: low
touches: ["fn:sample.items.list", "fn:sample.items.create", "rule.sample-retry"]
change_order:
  adds: ["fn:sample.items.create"]
  modifies: ["fn:sample.items.list", "rule.sample-retry"]
  retires: []
---

## Goal
The atlax fields: touches (the designations the card's diff touches) and
change_order (the authored elements it adds, modifies and retires). The
change order freezes at the commit where the card's status first becomes
now (or doing).
