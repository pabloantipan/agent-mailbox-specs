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
   hand. Never a button that does nothing.
5. Copy controls are keyboard reachable, and taking one does not destroy the
   selection it exists to serve.

## 5. Long bodies, real times

1. A body past roughly ten lines collapses behind a `show all`. The seats post
   long; §4's copy always takes the whole body regardless of what is shown.
2. Every age carries its absolute time, at minimum in a `title`. Relative ages
   stay the primary rendering — *"Ages are the whole point of this view"* was a
   deliberate call and it stands.

## 6. Verification gate

Run against the live cell on `127.0.0.1:9494`, and items 4 and 12 also against
the record with the same directory as `RECORD_UI_DIR`. Delivery claims are
proved in the database or through `/health`, never by reading the page that is
under test. Evidence goes on the card; this table has one writer.

| # | Item | Result |
|---|---|---|
| 1 | a reply sent from the page carries `parent_id`, and the thread renders it indented under its parent with a breadcrumb naming the parent's author and kind | — |
| 2 | a reply target clears back to top-of-thread without leaving the thread; a message whose parent is absent renders at top level rather than vanishing | — |
| 3 | a reply addressed to one seat writes that seat into `to_agent` and becomes undelivered for that seat only — checked in the DB, not on screen | — |
| 4 | an unaddressed reply still broadcasts, and the composer said `wakes N seats` before it was sent | — |
| 5 | `@` opens the roster; choosing a seat sets the recipient and removes the token from the body; an `@` typed and not chosen leaves the recipient untouched and the body intact | — |
| 6 | the picker shows watcher state, and a seat that is `deaf` reads as deaf before the send | — |
| 7 | a selection inside `#msgs` survives at least three poll cycles on an unchanged thread, and the pane still shows a new message within one cycle of its arrival | — |
| 8 | copy-message yields the body as posted, byte for byte, including `<`, `>` and `&` | — |
| 9 | copy-thread yields every message with author, recipient, kind and absolute time, in order | — |
| 10 | with `navigator.clipboard` deleted from the page, both copy paths still put the text somewhere the reader can take it | — |
| 11 | a long body collapses and expands, and copy still takes the whole body; every age carries its absolute time | — |
| 12 | the same three files render under `DISCUSS_UI_DIR` and `RECORD_UI_DIR` with no fork beyond the presence tests §2, §3 and §4 name | — |

Item 7 is the one to design for. It is the reason this card exists, and the
easiest thing to break with a naive "always re-render" or a naive "never
re-render" — one loses the selection, the other loses the cell.
