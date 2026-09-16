# The browser view — composer, threading and copy

Written against `ui@5245784` and `api@ff4c6e7`. `view-spec.md` was about what
the page failed to say about its own state; this is about what it fails to do
with what the server already hands it.

Every gap below is the page. `POST /messages` has accepted `{to?, parent_id?}`
since `discuss-spec.md` §2 was written and still does
(`api/internal/api/handlers.go:37`), and `GET /threads/{id}` has carried
`parent_id` on every message all along. Nothing here needs the API to change.

Rule 7 of `record-spec.md` binds this round as it bound the last: one page, two
servers, no fork. Three presence tests appear — `you`, the clipboard object and
the roster — and each must degrade to a working page, never a dead control.

## 1. A reply carries its parent

`messages.parent_id` is persisted, served, and populated: thread
`01M1N893SRYKX2F6H6G9WCCCMA` returns three messages whose parents are
`[-, 01M1N8…, 01M1N8…]`. `sendReply` (`ui/app.js:617`) posts
`{thread_id, kind, body}` and `openThread` renders a flat list. The page both
hides the tree the cell built and flattens every reply it sends into a
top-of-thread orphan.

Required:

1. `#msgs` renders the relation. A message with a parent is indented one level
   and carries a breadcrumb naming the parent's author, its kind, and the
   opening of its body. Indentation stops after three levels; past that the
   breadcrumb alone carries the relation, because the pane is 680px and a
   staircase is not a tree.
2. Replying to a specific message sets `parent_id`. A reply with no target is
   top-of-thread, as today.
3. The composer states what it is replying to and offers a way out: the target
   clears back to top-of-thread without leaving the thread.
4. A message whose parent is not in the thread renders at top level. It must
   never disappear.

## 2. A reply carries a recipient, and `@` is how you pick one

`#rto` is a `<span>` reading "into this thread" (`ui/app.js:404`) and
`sendReply` sends no `to`. An empty `to` is stored as `to_agent IS NULL`, which
`Undelivered` delivers to every agent in the project except the sender
(`api/internal/store/store.go:458`). **Every reply typed into this page wakes
the whole cell, and nothing on screen says so.** New threads can be addressed;
replies cannot.

`to` is one column. There is a single recipient or there is a broadcast — which
is why `@` here cannot mean what it means in Teams. A mention there is a
notification hint inside free text and you may write five. Here it is the
address, and it decides who wakes.

Required:

1. The reply composer has a recipient control. It defaults to the author of the
   message being replied to, and to broadcast when there is no target.
2. `@` in the composer opens the roster, filtered as typed. Choosing a seat
   sets the recipient and **removes the token from the body**: the body stays
   prose, the recipient lives in the field the store actually has.
3. An `@` token that was not chosen from the picker is prose and changes
   nothing. Writing "as @nico said" to Andrea must not retarget the message. A
   convention that silently moves a wake is worse than a dropdown.
4. `@everyone` sets broadcast.
5. Nothing about addressing is duplicated into the body. The rendered header
   already shows `from → to`.

## 3. The composer says what the send will cost

Required:

1. A live line beside the controls: `wakes 1 seat` for a direct message, the
   roster size minus yourself for a broadcast.
2. The picker carries each seat's watcher state, which `/health` already
   serves: `alive | stale | never`, and `deaf`. Addressing a seat whose watcher
   is dead says so before the send, not after nobody answers. Three of camp's
   five seats are on dead watchers as this is written.
3. With no roster the line says nothing. It never guesses a number.

## 4. Copy

The message pane cannot be copied from. `openThread` replaces
`#msgs.innerHTML` wholesale (`ui/app.js:443`) and `tick` calls it every four
seconds whenever the reply box is empty (`ui/app.js:472`); replacing those
nodes destroys any Range into them, so a selection does not survive one poll.
The guard for precisely this reasoning sits one line above — *"Never refresh a
thread while a reply is half-typed"* — and was never extended from the textarea
to the text.

Required:

1. The poll does not re-render `#msgs` when the thread payload is unchanged,
   and does not re-render while a selection is inside it. Extend the existing
   guard; do not grow a second mechanism beside it.
2. Copy one message: the body as posted. Not the truncated render, and not the
   HTML-escaped form — a body containing `<`, `>` or `&` round-trips exactly.
3. Copy the thread: every message as `from → to · kind · <absolute time>`
   followed by its body, in order, plain text. This is how a thread reaches a
   Claude Code session or a card.
4. `navigator.clipboard` is a presence test. `127.0.0.1` and https have it; the
   same three files served by the record over plain http on a LAN address do
   not. Without it, fall back to a pre-selected textarea the reader can copy by
   hand. Never a button that does nothing. Note for whoever tests this:
   `delete navigator.clipboard` is a no-op — it is an accessor on
   `Navigator.prototype`, so the property comes back and the real path
   runs. Use `Object.defineProperty(navigator, "clipboard", {value: undefined})`.
5. Copy controls are keyboard reachable, and taking one does not destroy the
   selection it exists to serve.

## 5. Long bodies, real times

1. A body past roughly ten lines collapses behind a `show all`. The seats post
   long; §4's copy always takes the whole body regardless of what is shown.
2. Every age carries its absolute time, at minimum in a `title`. Relative ages
   stay the primary rendering — *"Ages are the whole point of this view"* was a
   deliberate call and it stands.

## 6. Verification gate — PASSED 2026-09-06

Run 2026-09-06 against the live cell `camp` on `127.0.0.1:9494`, with items 10
and 12 also against the record on `127.0.0.1:8090` serving the same directory
as `RECORD_UI_DIR`. Neither server was restarted. Delivery was read out of
`~/.local/state/discuss/discuss.db`, never off the page under test. Evidence is
on `working-on/done/view-composer.md`; this table has one writer.

| # | Item | Result |
|---|---|---|
| 1 | a reply sent from the page carries `parent_id`, rendered indented under its parent with a breadcrumb naming author and kind | **pass** — `01M1TQRZV7…` carries `parent_id = 01M1TKVYYE…` in the DB, rendered at `margin-left:22px` under `↳ designer_mauricio · msg · here` |
| 2 | a reply target clears back to top-of-thread; a message whose parent is absent renders at top level rather than vanishing | **pass** — clearing leaves `openThreadData.id` unchanged; a forced `parent_id = NOSUCHPARENT` renders at `0px` with the message count intact |
| 3 | a reply addressed to one seat writes that seat into `to_agent` and is undelivered for that seat only — checked in the DB | **pass** — `to_agent = designer_mauricio`; `Undelivered` evaluated per seat across the six-seat roster returns that seat and no other |
| 4 | an unaddressed reply still broadcasts, and the composer said `wakes N seats` before the send | **pass** — `to_agent IS NULL`, the same query returns five seats; the line read `wakes 5 seats — 4 of them not picking up`, and `wakes 1 seat — designer_mauricio is not picking up · 7 waiting 42m` before the addressed one |
| 5 | `@` opens the roster and sets the recipient while removing the token; an `@` not chosen leaves recipient and body untouched | **pass** — `ping @javi` picked leaves body `"ping "` and `to = designer_javiera`; `as @nico said, ship it` leaves the picker shut, the body byte-identical and `to = ""` |
| 6 | the picker shows watcher state, and a `deaf` seat reads as deaf before the send | **pass** — row `designer_mauricio · alive, not picking up` carries `opt deaf`, and the wakes line repeats it on selection; `/health` says `deaf: true` |
| 7 | a selection survives three poll cycles on an unchanged thread, and the pane still shows a new message within one cycle | **pass** — the same Range returns the same string and the same node object after three 4.2s cycles; a live arrival raised the count by one with the selection intact |
| 8 | copy-message yields the body as posted, byte for byte, including `<`, `>` and `&` | **pass** — equals `openThreadData.messages[i].body`, 381 chars, including `<script>alert("x")</script>`, `a<b`, `c>d`, `e&f` and a bare `&` |
| 9 | copy-thread yields every message with author, recipient, kind and absolute time, in order | **pass** — 5 header lines for 5 messages, each `from → to · kind · YYYY-MM-DD HH:MM:SS`, chronological |
| 10 | with the clipboard object absent, both copy paths still put the text where the reader can take it | **pass** — with `defineProperty` (see §4.4) both open the fallback textarea, focused and fully selected, holding identical text, and restore the borrowed Range on close |
| 11 | a long body collapses and expands, copy still takes the whole body, and every age carries its absolute time | **pass** — clamps 210px, opens 356px, closes back; copy returns all 381 chars while collapsed; every `#msgs time`, `.thread .age` and `#roster .who` carries a `title` |
| 12 | the same three files render under both servers with no fork beyond the presence tests §2, §3 and §4 name | **pass** — `app.js` and `style.css` byte-identical from disk, mailbox and record by sha1; on `:8090` `injected === false`, `roster.length === 0`, the recipient control unrendered, the wakes line empty, no page errors |

Item 7 is the one this card existed for. A naive "always re-render" loses the
selection and a naive "never re-render" loses the cell; only one of those is
visible, which is how it would have shipped.

**§6 item 10 after `view-spec.md` §8 (2026-09-16).** Item 10 passed by removing
`navigator.clipboard` and watching the fallback textarea open. Under §8 a page
with no clipboard renders no copy control at all, so the item as written cannot
run. Its replacement is §8's clipboard row; the fallback now serves only a
clipboard that exists and refuses, and says so.

## 7. What the run found that this spec did not anticipate

**`you` is what makes the wake count sayable.** §3.1 says "roster size minus
yourself", which is only computable because `health-you-field` landed the round
before. The two cards are coupled and this spec treated them as independent.

**A note to self wakes nobody.** `Undelivered` filters `from_agent != ?`, so a
message from you to you reaches no one. The recipient control offers your own
seat, because the pane already renders `note to self` — so the line needed a
third case rather than claiming `wakes 1 seat`, which would have been this
spec's own error in the other direction.

**Suppressing `mousedown` is what makes §4.5 true.** A click on a control
inside `#msgs` collapses the selection before any `click` handler can read it,
so an in-pane copy button destroys the selection it exists to serve regardless
of what the handler does. The three in-pane controls now `preventDefault()` on
`mousedown`; Tab still reaches them and fires `click` with no `mousedown`.

**A reconciler that replaces a node must move its cursor with it.** Found by
gate item 2: replacing the node the insertion cursor points at leaves the
cursor outside `#msgs`, and the next `insertBefore` throws and takes the pane
down. One line, `ui@06aad49`. A "never re-render" reading of §4.1 would never
have surfaced it.

**Rule 7 now costs three presence tests, and there is no rule for the
fourth.** `you`, the roster and the clipboard object each degrade cleanly, but
they are three independent branches in one file with no shared statement of
what a degraded page is allowed to look like — and the connect panel's path
mismatch is a fourth waiting. Open: `working-on/view-degradation-rule.md`.
