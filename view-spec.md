# The browser view — refinement spec

The three files in `ui/`. Two servers render them and neither compiles
anything: `discuss-api` under `DISCUSS_UI_DIR`, which injects one token per
cell it was told to expose, and the record under `RECORD_UI_DIR`, which
injects nothing and leaves the page to ask. Rule 7 of `record-spec.md` is the
constraint on everything below: **one page, two servers, no fork.** A
difference between the two is a presence test on a field, never a branch on
which server is answering.

This spec is written against `ui@42d7d37` and `api@d74eaa1`. It covers five
defects found reading the page on 2026-09-05, in the order they cost a reader.

## 1. Alerts and errors are two surfaces, not one

`#alerts` has two owners that overwrite each other. `renderRoster`
(`ui/app.js:210`) rewrites it every 4s from the poll; `fail`
(`ui/app.js:135`) writes transport errors into the same node. Whichever ran
last wins, so a failed search is erased by the next healthy tick, and a
deaf-agent alert is erased by an unrelated fetch error. The page's whole
reason to exist is telling a human that the cell stopped being woken; a
surface that erases itself every four seconds cannot do that.

They are different kinds of fact and must not share a node:

| | Alerts | Errors |
|---|---|---|
| Are | state of the cell, derived from `/health` | events in this browser's last request |
| Owner | `renderRoster`, on every tick | the call site that failed |
| Lifetime | rewritten wholesale each tick — correct, they are derived | until dismissed or until the same operation succeeds |
| Examples | `nico is not picking up`, `the record is 2m behind` | `API unreachable`, `search failed`, a rejected post |

Required:

1. Two regions. `#alerts` keeps its name and its single owner. Errors get
   their own region, and nothing else writes to either.
2. Errors are keyed by source. `fail(source, msg)` and `clear(source)`; a
   search failure never clears a poll failure, and a successful `tick` clears
   only the poll's error. This is what makes "until the same operation
   succeeds" implementable.
3. Every error carries a dismiss control. An alert does not — dismissing a
   fact about the cell would be a lie the next tick corrects anyway.
4. Both regions are `aria-live="polite"`, and the error region is
   `role="status"`. Not `alert`: a region rewritten on a 4s poll must never
   take focus.
5. Empty means zero height. Neither region reserves an empty bar.

## 2. The page says who is reading it

The header reads `discuss · camp`. Every post the page makes is attributed to
an identity it never names, and the identity is not derivable in the page —
the token is opaque to it and the injected map is keyed by project, not by
agent. Only the server knows.

**API.** `GET /projects/{pid}/health` gains a top-level `"you": "<agent>"`,
taken from the caller's `Identity` (`api/internal/api/auth.go:14`), which is
already resolved before the handler runs. It is a top-level field, not an
`agent_fields` or `thread_fields` entry, so `specs/health-golden.json` does
not change and the record needs no change to keep serving the same shape.

**Page.** The header renders the reader when `you` is present and renders
nothing when it is absent. That presence test is the whole of the
local-versus-central difference here — no branch on the server, per rule 7. A
record that later wants to name its viewer serves the same field and the page
already works.

The identity must survive a project switch: `use()` reconnects with a
different token and `you` can change with it.

## 3. Search is a mode, and the page must say so

`runSearch` replaces the thread list with message hits (`ui/app.js:227`). The
only evidence of the change is that a `clear` button appeared. Worse, the
filter row still renders as pressed and does nothing — `visible()` is never
consulted for hits, so `chats` and `needs me` silently do not apply.

Required:

1. A result header above the hits, stating the query and the count.
2. The filter row is visibly inert while a search is showing. Silently
   ignoring a pressed filter is worse than disabling it.
3. `Escape` in the search box clears the search.
4. A zero-hit search keeps the query on screen. Today's `No message matches
   "x"` already does; keep it.
5. Clearing restores the filter and the selection that were in force before.

## 4. The first paint has a state

`connected()` unhides `#main` and calls `tick()`. Until the first response
lands, the thread list is an empty node and the roster is empty. On a slow or
failing first request the page reads as a cell with nothing in it.

Required: the thread list distinguishes **loading**, **empty** and **loaded**.
Loading is a line of text, not a spinner — this is loopback, and the point is
honesty rather than entertainment. `Nothing here.` must never render before
the first response. A first tick that fails is an error per §1, and the
loading line does not survive behind it.

## 5. The connect panel is the record's front door

It is not a fallback. It is the only thing a reader of the record ever sees,
and today it is an unlabelled card that cannot say which mailbox it failed to
reach.

Required:

1. The page names itself above the form. A reader who followed a link should
   know what this is before typing a token into it.
2. The error line distinguishes, and names the base URL and project in each:
   host unreachable, `401` (the token is not known to this server), `403`
   (the token is for another project), any other status. The four cases have
   four different next moves and the current text conflates them.
3. **An injected token the server rejects must reach this panel.** Today a
   rotated token leaves the page polling forever behind `API unreachable
   (401)` with no way to enter a new one — the panel exists and is
   unreachable, because `injected` is decided once at load. A `401` or `403`
   on the poll says so in a sentence and offers the form.
4. On success the panel says nothing extra; it becomes the view.
5. `autocomplete="off"` on all three fields stays. `forget this server` stays
   hidden in the injected case.

## 6. Verification gate — PASSED 2026-09-05

Run 2026-09-05 against the live cell `camp` on `127.0.0.1:9494`
(`DISCUSS_UI_DIR`) and, for items 7 and 9, against the record on
`127.0.0.1:8090` with the same directory mounted read-only as
`RECORD_UI_DIR`. Neither server was restarted for the page's sake; the API
was restarted once for item 3. Evidence is on the two cards in
`working-on/done/`; this table has one writer.

| # | Item | Result |
|---|---|---|
| 1 | a deaf-agent alert and a failed search are both on screen at once, and four seconds later both are still there | **pass** — deaf `designer_javiera` and `search failed (500)` together, identical five seconds later across a tick |
| 2 | an `API unreachable` error clears on the next successful tick and not before, and a search failure does not clear it | **pass** — a second failing tick and a fresh search failure both leave it standing; health returns and it clears alone, the search error staying |
| 3 | `/projects/camp/health` returns `"you":"pablo"` for pablo's token and `"you":"po_andrea"` for andrea's; `go test ./...` green; `specs/health-golden.json` unchanged | **pass** — top-level keys `[agents, now, project, push, threads, you]`, agent rows unchanged, golden untouched; `TestHealthNamesTheCaller` and `TestHealthKeepsYouOutOfTheRows`, `go vet` and `go test ./...` clean |
| 4 | the header names the reader on 9494, and names nobody when the payload carries no `you` | **pass** — `you are pablo` from the live field; with `you` stripped the header names nobody, at zero height |
| 5 | a search states its query and count, the filter row is inert while it shows, and `Escape` clears it | **pass** — `1 message matches "outbox".` above the hits, all four filter buttons disabled and dimmed, Escape restores both the filter and the thread that was open |
| 6 | from a cold load the list says it is loading, then shows threads; `Nothing here.` never precedes the first response | **pass** — sampled at 1ms in a same-origin iframe: `""` @11ms, `Reading the mailbox…` @21ms, threads @42ms. A failing first tick replaces the line with `The mailbox has not answered.` |
| 7 | the connect panel distinguishes unreachable / 401 / 403 / other, naming the base URL and the project | **pass** — four sentences, each carrying the base URL and the project it tried |
| 8 | a rotated token on an injected page produces a sentence and a way back to the form, not a silent poll loop | **pass** — the poll stops (zero `/health` calls in the next 4.5s), the panel appears with the 401 sentence, base and project prefilled, focus on the empty token field |
| 9 | the same three files render under `DISCUSS_UI_DIR` and under `RECORD_UI_DIR` with no fork in the code beyond the presence tests §2 and §5 name | **pass** — `app.js` and `style.css` byte-identical from both servers by sha1; `index.html` differs only in the two injected values; on `:8090` `injected === false` and the connect panel is the page |

Item 9 is the one that protects the design. If it fails, the page has grown a
second implementation and rule 7 is gone, whatever the other eight say.

## 7. What the run found that this spec did not anticipate

**`[hidden]` did not hide.** `#connect`, `#cell` and `#detail` each set
`display:flex` with an id selector, which outranks the UA sheet's
`[hidden]{display:none}`. Every `.hidden = true` in `app.js` was inert: the
connect panel was painted over the thread list on every load, and the cell
panel over the thread detail. The page had been asking for a token in front
of a working view — the injection was never broken, the hiding was. Fixed in
`ui@57f5f9e` with one declaration; no logic changed, because none of it was
wrong. This is why §1 through §5 were all read as separate defects: they were
symptoms behind one.

**The connect panel cannot reach the record.** §5 calls it the record's front
door, and it is — but the page builds `/projects/{project}/health` while the
record serves `/v1/factories/{factory}/health`. `:8090/projects/camp/health`
is a 404. The shapes match per rule 7; the paths do not, and the form has no
field for a cell. Open, and Pablo's to settle: see
`working-on/view-connects-to-record.md`.

**`make install` couples the two binaries.** `install` depends on `build`,
which builds `discuss-hook` from the working tree — so an api-only card
cannot use the documented install path while the hook is dirty without
shipping another card's in-flight change. Worked around by hand; a per-binary
`install-api` target is the fix.
