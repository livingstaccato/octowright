# session — the per-browser session object

Extracted from the root `AGENTS.md` so it loads only when you work in this
directory. The root file remains the canonical index.

### Browser Session Operation Gate

Every `BrowserSession` owns one `SessionOperationGate` (`src/octowright/session/operation/gate/core.py`) that serializes Octowright-owned Playwright work FIFO within that session while leaving different sessions fully parallel: the exact owning `asyncio.Task` may re-enter (a compound operation calling existing session helpers doesn't deadlock), but a task the owner spawns is a different identity and queues behind it like anyone else. One macro run — including nested `macro_call` actions, a full `macro_run_sequence`, macro-artifact replay, capture-and-close, and a closing handoff/fluid relaunch of the source session — holds one root lease for its entire invocation so a manual action can't interleave mid-sequence. Ordinary admission is bounded by `OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS` (default `300`, must be positive finite seconds; `BrowserPool(operation_queue_timeout_seconds=...)` takes precedence over the env var) — this is queue wait only, separate from and added on top of any Playwright action/navigation/expect timeout, and the gate never retries a browser operation. A configuration at or above the 600-second progress-heartbeat ceiling (`OCTOWRIGHT_HEARTBEAT_MAX_SECONDS`) is allowed but logs `octowright.pool.operation_queue_timeout_exceeds_heartbeat_ceiling` because a caller stuck that long in queue may lose bridge transport visibility before it is ever admitted. A normal close establishes a cutoff, drains everything already admitted or queued, and only then tears the session down; work arriving after the cutoff is rejected with `SessionClosingError` rather than queued, and the close outcome is durable — cancelling the calling task does not revoke an accepted close or strand the session. External browser/page/context closure (not routed through the gate) can still interrupt whatever operation is actively running; any operation still queued at that point fails with `SessionClosedError`. All gate error kinds (`SessionBusyTimeoutError`, `SessionClosingError`, `SessionClosedError` — plus its `SessionCloseAbortedError` subclass — `OperationGateInvariantError`, and `SessionOperationAbortedError`, both described below) are session/tool-scoped — they never mean the MCP transport should be restarted, and a broken gate is isolated to its one session. `BrowserSession.list_pages()`, `list_frames()`, and `set_dialog_policy()` are `async` (they read/mutate active-target state under the gate) — any embedder calling them directly must `await` them, and should tear a session down through `BrowserPool.close()` rather than raw Playwright teardown so the close cutoff/drain semantics apply. `session.operation_snapshot()` / the optional field `BrowserPool.list_sessions()` adds returns only `{state, active_operation, active_for_ms, queue_depth, oldest_wait_ms, queue_timeout_seconds}` — fixed operation identifiers and timing/depth counters, never a selector, URL, credential, macro argument, or task identity. The same snapshots for every live browser session are also available in one call at `octowright_status()["pool"]["operation_gates"]` (each entry adds `instance_id` and `kind`), the fastest way for an agent or operator to check whether a specific session's gate is stuck. `OperationGateInvariantError` (the fourth gate error) means that one session's gate reached an inconsistent internal state and is now permanently `broken` — it is not a transport or daemon problem; relaunch that one session and move on. An **active-duration ceiling** is a separate, OFF-by-default backstop that reaches the same `broken` outcome from a different direction: `OCTOWRIGHT_OPERATION_ACTIVE_TIMEOUT_SECONDS` (unset, or a falsey token, disables it) covers the Playwright call site nobody has bounded with `session/timeouts.bounded` yet, rather than the ones already wrapped. Instead of a per-gate timer, the periodic housekeeping loop (job 6 — see `housekeeping.py`'s module docstring) checks each live session's gate once per cycle against what it already tracks — `_active_since`/`_root_operation` — via `SessionOperationGate.enforce_active_timeout`; a breach cancels the owning task and drives that ONE session's gate to `broken` through the same `_break_locked` invariant path every other break uses, so a LATER operation is rejected with the ordinary `OperationGateInvariantError` message. The one caller actually cancelled is a separate case, and gets `SessionOperationAbortedError` (a `RuntimeError`) rather than a bare `asyncio.CancelledError`: that owning task is typically the MCP request task, `CancelledError` is a `BaseException` the mcp library does not convert, and the JSON-RPC dispatcher answers `CONNECTION_CLOSED` — which would kill the whole connection including concurrent healthy calls, and tell the agent the daemon is dead. `operation()` therefore absorbs the ceiling's OWN cancellation, distinguishing it from a genuine one (client disconnect, daemon shutdown, which still propagate as cancellation) by `uncancel()`-ing back to the cancelling count captured when the task took ownership — `asyncio.timeout.__aexit__`'s own pattern. The absorption covers the `finally`'s release as well as the body: a `finally` is a SIBLING of the `except` clause rather than nested in it, so without its own wrapping a breach landing in the one-scheduler-iteration window between "body returned" and "release completed" would escape unabsorbed with the full blast radius. Checking is per-session and isolated, so one wedged session's breach can never touch another session's gate in the same cycle. The check acts on `OPEN` **and** `CLOSING` (never `CLOSED`/`BROKEN`, which have no active lease left to break): `reserve_close` queues a close reservation's waiter behind whatever owner already holds the gate rather than granting it, so checking `OPEN` only would disarm the ceiling the instant a close was requested — exactly when a human or agent reaches for it — and leave `reservation.wait()` hanging forever. Breaking while `CLOSING` still resolves the close instead of stranding it (`_fail_queued_locked` fails the queued waiter, and the close coordinator's own `finally` still drains `_sessions`/`_closing_sessions` regardless of whether the close body itself ever ran) — pinned end to end by `tests/test_operation_gate_integration.py::test_active_timeout_ceiling_unwedges_a_close_in_progress`. A ceiling breach that instead cancels a close reservation already GRANTED and mid-teardown (e.g. a hung `context.close()`) does not resolve through `_break_locked` at all: such a cancellation can land in more than one place — swallowed and returned by `close_helpers.prepare_then_teardown`, or raised in the close body before it ever runs — and every one is normalized in a SINGLE seam, `_terminal_close_failure` (`operation/gate/close.py`), into `SessionCloseAbortedError`, a `SessionClosedError` subclass (`operation/gate/types.py`). `_release_close` deliberately converts nothing itself, so one cause cannot yield two error types depending on where it landed. That subclass distinguishes "teardown was aborted, the browser's close state is unconfirmed" from a plain `SessionClosedError` (an external close winning the race BEFORE any teardown ran, browser confirmed torn down either way). `relaunch._close_with_fallback_snapshot` relies on that distinction to refuse treating an aborted close as its ordinary safe-race fallback -- letting it propagate instead of silently discarding a preparation snapshot for a stale pre-close read and launching a replacement over an unconfirmed teardown (Chrome's `SingletonLock` on a persistent profile) -- pinned end to end by `tests/test_handoff.py::test_handoff_close_aborted_by_ceiling_propagates_instead_of_stale_snapshot`. This is deliberately a DIFFERENT signal from a per-call `SessionCallTimeoutError` escaping a gated operation (the `on_call_timeout` hook described above): cancelling from the outside delivers a plain `asyncio.CancelledError` with no `__cause__` chain back to a `SessionCallTimeoutError`, so `on_call_timeout` does not fire for a ceiling breach PROVIDED the gated code under the cancelled task does not itself convert that `CancelledError` into a different exception type — true of every call site checked today, though not a language-level guarantee — so in practice one wedge produces exactly one signal, not both an `unresponsive` `SessionCrashedEvent` and a contradicting ceiling-broken gate. Telemetry is the same shape: six bounded metrics, all under `octowright_operation_*`, with attributes limited to the fixed operation name, browser `kind`, and outcome/reason — never an instance ID — `octowright_operation_queue_wait_seconds` and `octowright_operation_active_duration_seconds` (histograms), `octowright_operation_queue_timeout_total`, `octowright_operation_active_timeout_total`, and `octowright_operation_rejected_total` (counters), and `octowright_operation_queue_depth` (a gauge aggregated per browser `kind`, not per session or operation). Gate scheduling itself is never written to JSONL, replayed, exported, or otherwise surfaced through the macro pipeline — only the underlying behavioral action is. Accessible keyboard drag/drop (`browser_a11y_dragdrop` / `session.a11y_dragdrop`) is gated like any other session operation and replayable as the `a11y_dragdrop` macro action. A control-lease/"Take control" workflow, terminal-session gating, and a repo-wide DRY audit are out of scope for this gate.

### Unbounded Playwright calls: the setup half

`session/timeouts.bounded()` covers `evaluate`, `title` and `content` -- the
calls a running page answers -- and also the context setup calls, because a
broken engine can leave those hanging while the page still answers. On a WebKit
build that cannot navigate to `about:blank`, `page.evaluate` answers in ~6s
while `context.expose_binding`, `context.add_init_script` and `context.route`
**never return at all** (measured with raw Playwright and no Octowright
imported). Playwright gives none of them a `timeout` either.

Unbounded, `browser_launch` wedges inside `_expose_viewport_binding`, several
steps *before* the `page.goto` whose own 30s timeout would surface the broken
engine as an ordinary error -- a bounded, reportable failure becomes an
unbounded hang, and the engine-health block never records anything. Bounded,
the same launch raises `SessionCallTimeoutError` in ~35s.

Every one of those call sites is wrapped, and the AST scan in
`tests/session/test_no_unbounded_calls.py` covers `add_init_script`,
`expose_binding`, `expose_function`, `route` and `unroute` alongside
`evaluate`, `title` and `content`, so a new setup call cannot quietly
reintroduce the hang. It also
covers the handle calls (`evaluate_handle`, `content_frame`, `owner_frame`,
`get_properties`, `json_value`, `dispose`), which a credential `type` makes
once per character: `octowright.credential_input` is stdlib-only (the exported
CLI renders it), so it bounds each whole step with one `asyncio.timeout` and
is allowlisted by function rather than wrapping each call in `bounded()`.

### Websocket observation

`page.on("websocket")` is wired at launch and every frame lands in the
per-session `.websocket.cache.jsonl` sidecar. `browser_websocket_messages` /
`browser_websocket_summary` are the read-back pair, named to match the HTTP
pair. They exist so a real-time app (an authenticated SPA pushing updates over
a socket instead of polling) can be observed without either alternative:
polling HTTP loses the real-time property, and lifting the page's session token
out of the browser to replay it externally is defeated by httpOnly cookies and
is something the network capture correctly will not hand over.

**playwright-python emits the frame payload itself.** The frame event's
argument is a `str`, or `bytes` for a binary opcode
(`_network.WebSocket._on_frame_sent` calls `emit(FrameSent, data)`). Only
**Node's** API wraps it in an object carrying `.payload`; reading `.payload`
off a `str` or `bytes` yields `None` for every frame, so the sidecar would
faithfully persist rows with no content. The handler therefore uses the
argument itself and keeps the attribute read only as a fallback
(`getattr(frame, "payload", frame)`), so a binding that grows a frame object
does not silently go empty. The live test asserts on the payload bytes, not
just a row's shape, because a shape-only assertion cannot catch an empty
payload.

Frames are read from the sidecar rather than an in-memory ring: the sidecar is
already the full-fidelity sink, and a parallel in-memory copy would double the
footprint of a firehose page to serve a question nobody may ask. Reads go
through `recorder.tail_log_lines`, which already bounds one read by bytes,
lands the cursor on a line boundary and steps over an oversized line instead of
freezing -- all of which a socket carrying multi-megabyte frames will exercise.
A read flushes the batched write buffer first, or it would return everything
except the most recent frames, which are the ones someone watching a live
stream wants. The flush restarts the batching clock as well as the frame
counter: resetting only the counter would make the next frame written see a
stale stamp and flush again immediately, undoing a batch's worth of the
syscall batching.

**Lines rather than parsed events, because `next_cursor` has to name a row.**
`tail_log` returns a window of parsed dicts and the offset of the window's END,
which is the right answer only for a caller that consumed all of it. A capped
read does not, so a window-end cursor would skip every frame past the cap: at
the defaults (cap 100, 8 MiB window) a socket that emitted 5,000 frames would
return 100 and silently lose 4,900. `recorder.tail_log_lines` yields each line
with its absolute offset, so the page can end at the first frame it did NOT
return and resume exactly there -- the rule `core_network_mixin._page_requests`
already states and the reason a *matching* row's offset is used rather than the
row after the last one returned. It splits on `b"\n"` rather than
`splitlines()`, whose extra separators would make the running offset disagree
with the bytes on disk, and it splits a **chunk at a time**: a generator over a
whole-blob split is only lazy in appearance, since the split runs in full on
the first `next()` -- 5.51ms and a second copy of an 8 MiB window for a caller
that stops after 100 rows, against 0.126ms chunked, and within noise when the
whole window is consumed. `tail_log` deliberately does **not** route through
it: its three callers all discard the offsets, and pairing them costs a
generator resume, a tuple and an addition per line (+7% on an 8 MiB window,
+16% on a 1 MiB one), so it keeps one C-level split of the whole blob. What the
two share is `_read_window` -- the byte bound, the line boundary and the
oversized-line skip -- which is the part worth not duplicating.

**`truncated` means "page again and you will get more", and both ways a page
can be short qualify.** Reporting only the row cap would leave a caller
following "keep paging while truncated" holding a prefix whenever the byte
window cut the file first. But `next_cursor < total_bytes` alone is the same
mistake mirrored: `_read_window` deliberately HOLDS the cursor on an
unterminated trailing line, because the writer is mid-frame and those bytes
cannot be parsed yet, so a socket that is merely mid-write would report "more
to read" against a cursor that cannot move, and an agent told to page on it
would poll forever. The flag therefore also requires that this call actually
advanced the cursor, and the raw fact is published separately as
`more_on_disk` for a caller watching a live stream. `browser_tail_recording`
applies the same two rules under `max_events`.

**`browser_tail_recording` is bounded by response SIZE as well, and with no
`max_events` at all.** `tail_log` bounds the *read* at
`OCTOWRIGHT_TAIL_MAX_BYTES`, which is a memory bound on the leader rather than
a bound on what crosses the MCP transport, and the row cap is opt-in -- so
without a size bound a recording of fat rows (a console line carrying a
stringified API response) would cross the transport as a whole window in one
response. `TAIL_RECORDING_MAX_RESPONSE_BYTES` applies on both raw paths,
measured against the raw JSONL line rather than a re-serialization of the
parsed event (within a byte or two, and free). It needs no new field to report
itself: `cursor` already names the first event not returned and `complete`
already means "you have reached the end", so a caller paging until `complete`
gets everything, in several responses when the window is large. As with the
websocket budget, the first event is returned even when it alone exceeds the
limit, or the caller pages forever on a row that can never fit.
Separately, `capture_truncated` reports frames dropped at CAPTURE time by
`OCTOWRIGHT_WEBSOCKET_MAX_BYTES` (256 MiB unless set; `0` turns it off). The
read path recognizes the recorder's `websocket_truncated` marker rather than
skipping it as a non-frame row, and the field is separate from `truncated`
because, unlike a short page, dropped frames are unrecoverable. The marker
alone is not enough to report it, though: it is written ONCE, at the end of the
sidecar, so a page whose window does not happen to contain it would answer
`false` -- which is every page after the one that saw it, and every caller
resuming from a later cursor. The **session** seeds the field from its own
`_websocket_truncated` state, so the answer holds on every page; the marker
still stands on its own for a reader working from the file alone.

**A row cap does not bound size.** `limit=1000` with `include_payloads=True`
against a socket carrying multi-megabyte frames would put hundreds of MB on the
MCP transport in one response, the lesson `MACRO_FAILURE_CONSOLE_TEXT_CHARS`
records. A page also ends at `WEBSOCKET_MESSAGES_MAX_RESPONSE_CHARS`
(sized above the worst-case default read, so an ordinary call never meets it),
and one frame's body is capped at `WEBSOCKET_PAYLOAD_MAX_CHARS` with
`payload_truncated` set. The budget counts **every returned string** plus a
per-row structural constant, not just the payload fields: `url` is chosen by
the page, has no length cap and repeats on every row, so counting payloads
alone would let a socket with a very long URL return a thousand rows of
megabytes with the counter still under the limit. A base64 payload is cut on a
4-char boundary so the prefix still decodes -- cutting anywhere else hands back
a string that raises on `b64decode`, which reads as a corrupt capture rather
than as truncation. The budget deliberately still returns the FIRST frame even
when it alone exceeds it, or a caller would page forever on a frame that can
never fit.

**Neither read tool takes the session operation gate.** One reads a file and
the other reads a dict; no Playwright call is involved. Taking the per-session
FIFO lease would queue a poll behind whatever browser work was running -- the
whole of a `macro_run_sequence`, up to the 300s queue timeout -- which defeats
the "follow a live stream" workflow they exist for. `browser_tail_recording`,
the closest analogue and also a pure tail read, resolves its session the same
way. `cursor` arrives as an LLM-supplied int, and a negative one reaches
`fh.seek` and comes back as a bare `OSError: [Errno 22]`. It is clamped in
`tail_log_lines`, which guards that syscall for every caller, and again in
`read_frames`, which compares against it to decide whether a page advanced.
The MCP tools deliberately do **not** clamp a third time: three statements of
one rule, each with its own justifying comment, is how the next reader ends up
adding a fourth.

**The recorded preview is short; the sidecar's is not.** The frame preview is
written to the MAIN session JSONL as well as the sidecar -- a file whose only
ceiling is a generous 512 MiB (`OCTOWRIGHT_RECORDING_MAX_BYTES`) that
`browser_tail_recording`, the dashboard event stream and
`capture_create(kind="recording")` all read on behalf of callers who never
asked about websockets. The main recording gets
`WEBSOCKET_RECORD_PREVIEW_CHARS`; the sidecar keeps the long preview, since
that is what the read tools serve from. `payload_size` is the frame's real
length in both, so capping the text costs a reader nothing it needed.

**Payloads are previews by default**, with `include_payloads=True` for the full
body, mirroring `include_headers` on the HTTP pair and for the same reason: a
busy socket emits thousands of frames. Text and binary stay separate keys
(`payload_text` / `payload_b64`) so a caller decoding base64 never has to guess
which it is holding. Honest scope on redaction: a frame is application data
with no name to classify on, so unlike a header there is nothing to key a
policy off. What the capture can scrub is a value the session already knows is
secret: once the session privacy ledger holds one (a macro credential, or a
password `OCTOWRIGHT_REDACT_INPUTS` hid from a `fill`/`type` row),
`_scrub_websocket_entry` scrubs it from a frame's `url`, `payload_preview` and
`payload_text`, and a binary frame containing it is not stored at all
(`payload_redacted`, size kept), since bytes cannot be edited safely in place.
Beyond that, previews are length-capped at capture time, one frame's stored
payload is capped at `WEBSOCKET_FRAME_MAX_BYTES` (`payload_truncated`, true
`payload_size` kept) and full payloads are opt-in, which bounds volume rather
than sensitivity.

`browser_websocket_summary` answers "what is connected right now" without
replaying the JSONL's open/close *events*. The
registry is bounded (`WEBSOCKET_REGISTRY_MAX`) because a page can open a socket
per retry indefinitely; eviction takes **closed sockets first**, since evicting
a live one to retain a finished one answers the question wrong, and the
discarded count is reported so a shrinking total is explainable. It returns
**copies** of the registry entries: `list(registry.values())` copies the list
and not the dicts inside it, which the frame handler keeps mutating -- the same
reason `_select_console_tail` copies its entries.

**The registry key is a session-issued id, never an object address.**
playwright-python's `WebSocket` has no `.id`, and `id(websocket)` is not used
because CPython reissues an address once the object is freed, so a page opening
a socket per retry could hand a NEW socket the key of a finished one,
overwriting its record and merging two sockets' frames into one stream under a
single `socket_id`. A per-session counter cannot collide however churny the
page is. A binding-supplied `.id` is deliberately **not** used as the key
either: nothing guarantees it is unique within the session and
`_register_websocket` overwrites on a repeat, so believing a binding that
handed out a duplicate (or the literal `ws-1`) would reopen the merging bug the
counter closes. It is kept beside the key as `binding_id`, where it can be
correlated without deciding identity. `browser_websocket_messages` also
stringifies `socket_id` to match the summary's `id`, which
`_register_websocket` coerces, so a caller joining the two by dict key or `==`
gets matches.

**A socket is registered only once its listeners attach.** Registering first
would leave a socket whose wiring failed (or that had no `.on` at all) in the
table with no `close` handler to ever set `closed_at` -- permanently "open",
and since eviction prefers closed entries, evicted LAST, so a page that tripped
this repeatedly would push out genuinely live sockets: the exact outcome the
eviction ordering exists to prevent.

### Accessibility-snapshot credential scrubbing

Playwright renders a text-ish control's **value** as its accessible name, and the accessibility tree has no notion of `type=password` — a filled password box comes back as `- textbox: hunter2`, byte-identical in shape to a username box (verified against real Chromium). Unscrubbed, every aria sink would emit cleartext credentials: `browser_snapshot`, `browser_brief` (in the **core** profile), `capture_create`, `golden_save` (which persists them to disk indefinitely), `browser_capture_and_close`, the dashboard session detail, and `_resolve_semantic_metadata` — whose parsed `role` lands in the **JSONL recording** on every click, bypassing `OCTOWRIGHT_REDACT_INPUTS` in its default configuration.

`OCTOWRIGHT_REDACT_INPUTS`'s input classification cannot cover this on its own: it classifies a *typed value* at the moment of `fill`/`type` by inspecting the target element, and an aria snapshot is neither. Both paths read one policy resolver, so `passwords` (the default) means the same thing on both.

Every sink routes through `session/aria_redaction.aria_snapshot(locator)`; a test (`tests/aria_redaction/test_no_unscrubbed_sinks.py`) AST-scans `src/` and fails on any raw `locator.aria_snapshot()` call outside the scrubber, because the leak is a property of every sink rather than one bug in one place, and a new sink would reintroduce it. Design notes worth keeping:

- **Values are collected before the snapshot is taken.** If classification fails the call raises `AriaRedactionError` and no snapshot happens — there is no path that yields an unscrubbed tree because the classifier was unavailable. (Test doubles must therefore model the scan; `tests/_aria_stubs.py` provides it. `first.evaluate` serves both this scan and the record-time password probe, so the stub dispatches on the production JS constant by identity.)
- **Matching is value-based, not node-based.** The tree is a rendered string by then, so the only reliable join back to "which name was a secret" is the value, read from the DOM.
- Playwright **normalizes** an accessible name (a newline inside a value renders as a space), so each value is scrubbed in both raw and whitespace-collapsed form. It does *not* escape quotes/backslashes, so no unescaping is needed.
- Replacement is plain substring, **longest value first**, so a short secret can't eat a longer one it is a substring of. A 2-char password will also blank unrelated occurrences — the safe direction to be wrong in.
- Form controls in the light DOM and in every **open shadow root** (nested ones included) are read, because the tree renders an open shadow root's input values; a value inside a **closed shadow root** is not reachable by the scan.
- The result is then scrubbed of the **session privacy ledger** (`aria_redaction.ledger_scrubbed`), whatever `OCTOWRIGHT_REDACT_INPUTS` says: the scan sees only the inputs on the page now, and a value a macro typed that the page echoed as ordinary text is held only by the ledger. `BrowserSession.snapshot` scrubs its `url` and `title` the same way, so `browser_snapshot` and `golden_save` (which persists it) inherit both.
- `_parse_semantic_line` handles both accessible-name renderings (`button "Confirm Order"` **and** `textbox: tanuki-tim`); handling only the first would put the whole `role: value` string in `role`.

### Typing into a canvas: `key_mode="keys"`

`browser_type` sends Playwright's `page.type()`, which dispatches `keydown`
carrying the right `key`/`text` payload but **never holds the Shift modifier
down**. A DOM `<input>` reads that payload, which is why this is invisible on
ordinary forms. A canvas-based app — a KVM/BMC
console (AMI H5Viewer), a canvas terminal, anything drawing its own text
instead of using a real input — reads `code` + `shiftKey` and converts that to
HID scancodes. It never sees the payload, so Shift is silently dropped and
every shifted character lands as its unshifted twin: against a real H5Viewer,
`echo TYPE=Ab*:` arrives as `echo type=ab8;` —
`T`→`t`, `A`→`a`, `*`→`8`, `:`→`;`, with no error and no warning. On a BMC
console that is dangerous rather than merely wrong: a path silently losing its
`*` changes the command's scope.

`key_mode="keys"` presses physical keys with Shift genuinely held
(`_type_as_keystrokes`), so `shiftKey` is actually set. Three things about it
are load-bearing:

- **It is opt-in, not the default, and not auto-detected.** A character's
  physical key is a property of the *keyboard layout*, not of the character —
  `*` is Shift+Digit8 on US QWERTY and elsewhere on AZERTY — and nothing on
  the wire says which layout the target believes it has. `session/keyboard_layout`
  is therefore US QWERTY, the same assumption Playwright's own `code`
  generation makes. Defaulting to it would trade a silent failure on canvas
  targets for a silent failure on non-US ones. Sniffing for a `<canvas>`
  element is deliberately not done, because it would read as a
  guarantee: a target rendering its own text need not be a canvas (a `div`
  with a keydown handler behaves identically), so the detection would be
  right often enough to be trusted and wrong often enough to hurt. The tool
  description names the failure mode instead, which is what the LLM actually
  reads.
- **Keystrokes go through `session.page.keyboard`, element lookup through
  `session._target()`.** `Frame` has no `.keyboard` — only `Page` does — so a
  frame-scoped selector still resolves in its own frame while the keys go to
  the page. `browser_a11y_dragdrop` splits the two the same way. The one
  exception is a macro credential: `_keystroke` then presses through the
  focused element's handle (see `octowright.credential_input`), which is the
  page keyboard's press after a focus, so the held Shift still applies.
- **Shift is released in a `finally`, under its own 2s bound.** A latched
  modifier corrupts every later keystroke on that page, including another
  tool's, so a raising press must not leave it down. The bound exists because
  a credential step's `asyncio.timeout` cancels a wedged press exactly once:
  an unbounded release after it would hold the session gate forever against a
  target that answers neither. A release that does not
  answer (`core_page_mixin.shift_release_timed_out`) or raises -- a page
  closed under it raises `TargetClosedError` --
  (`core_page_mixin.shift_release_failed`) is logged, not raised, so the
  press's own outcome is what the caller sees. The bound is `bounded()`,
  caught inside the caller's gated operation, so a release timeout never
  escapes it to fire the gate's `on_call_timeout` hook and report the session
  unresponsive.

A character with no key on the layout (accented, emoji, any non-ASCII) falls
back to Playwright's own text insertion: it has no scancode to send, and a
guessed key would be worse than the payload. `key_mode` is recorded **only
when set**, so an ordinary `type` row stays byte-identical to a recording made
without it, and replay reproduces keystroke mode rather than silently
corrupting input the recorded run got right. The macro linter derives its
allowed fields from the method signature, so it accepts `key_mode` with no
separate entry.

### Keyboard (WAI-ARIA) drag-and-drop

`browser_drag` drives Playwright's `drag_and_drop`, a synthetic mouse sequence. It cannot operate a widget that implements only the **keyboard** WAI-ARIA APG pattern — grab with a key, move with keys, drop with a key — which is what accessible drag-and-drop widgets usually implement. `browser_a11y_dragdrop` is that counterpart.

One atomic attempt per call: grab → navigate → drop → poll-verify → release-on-failure. It deliberately does **not** retry or switch navigation strategy; that stays in the caller's orchestration, the same boundary `browser_click` draws by not retrying against alternate selectors.

**Exactly one `verify_*` field is required.** There is no universal cross-widget "it worked" signal, so a heuristic that sometimes works would be worse than an explicit contract: with no check the call would report success having confirmed nothing. Verification **polls** (`verify_timeout_ms` / `verify_poll_ms`) rather than checking once, because most drag flakiness is post-drop animation and reflow settling.

It **returns** its result on an ordinary failed verify instead of raising — a deliberate deviation from `expect_*`, which raises to abort a script when a precondition fails. This tool exists so the caller can decide what a failed drop means, and raising would force every caller into `try/except` just to read `stage_reached` (`failed_grab` | `navigated` | `dropped` | `verified` | `failed_verify`). It raises only when the result would be meaningless: the selector matches nothing, or the frame detached.

The release-on-failure path is the whole point rather than a nicety. A grab that succeeded with a drop that did not leaves the widget stuck in grab mode, which is **indistinguishable from a grab that never registered**. So a failed verify presses `release_key`, and so does any `Exception` raised after a successful grab, including one thrown by the caller-supplied `grabbed_predicate_js`. The one carve-out is a grab predicate that returns **False**: the key was pressed but the widget demonstrably never entered grab mode, so there is nothing to release.

**Task cancellation is the honest exception, and it does not release.** The engine catches `Exception`, and `asyncio.CancelledError` is a `BaseException` — so a cancel landing on the `await asyncio.sleep(...)` inside the verify poll, after a successful grab, unwinds without pressing `release_key` and leaves the widget grabbed. Making cancellation release would mean spawning a shielded task from inside the operation lease, which is exactly what the module's own docstring argues against (a spawned task is a different identity to `gated_operation` and would queue behind the lease its own parent still holds). A cancelled drag therefore needs a page reload, or an explicit `browser_press_key` of the release key, before the widget is usable again.

Two implementation constraints worth knowing before editing it. Keystrokes go through `session.page.keyboard`, not the active target: **`Frame` has no `.keyboard`** (measured; `Page` does), so a frame-scoped call would crash on the first press — element lookup still goes through `session._target()` so frame-scoped selectors resolve in their own frame. And the verify loop polls **in the calling task**: `gated_operation` re-enters only for the owning task, so a spawned helper calling back into a gated session method would queue behind the lease its own parent still holds and deadlock until the queue timeout.
