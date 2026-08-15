# Keyboard WAI-ARIA Drag-and-Drop Primitive (`browser_a11y_dragdrop`) — Design Spec

**Date:** 2026-08-13
**Status:** Approved design, pending implementation plan
**Topic:** Add a first-class MCP tool for driving keyboard-based (WAI-ARIA APG) drag-and-drop interactions — the accessible alternative to `browser_drag`'s mouse-only `drag_and_drop`.

---

## 1. Summary

`browser_drag` drives Playwright's `drag_and_drop` — a synthetic mouse sequence. It does not (and cannot) drive the **keyboard** drag-and-drop pattern that accessible widgets implement instead: grab an item with a key, move it with the keyboard, drop it with a key. That pattern showed up as a hand-rolled, project-specific implementation in an external test-automation tool (a real testing harness for a MAP-style assessment platform), where it was the direct fix for a class of flaky test failures: a stuck "grabbed" state left over from a failed drop, indistinguishable from a grab that never registered in the first place.

This is a generalization of that proven pattern into a reusable Octowright tool: `browser_a11y_dragdrop`, covering the two common WAI-ARIA APG interaction variants (Tab-cycle-to-target and Arrow-key-move) plus a raw-key-sequence escape hatch, with built-in poll-based verification and unconditional cleanup on any failure path.

### Decisions locked during brainstorming

| # | Decision | Choice |
|---|----------|--------|
| 1 | Nav variants (v1) | **Both** `tab` and `arrow`, plus a `keys` escape hatch for widgets that fit neither preset |
| 2 | Verification | **Caller-supplied predicate + convenience sugar** (`verify_js` plus `verify_selector_appears`/`verify_selector_gone`/`verify_text_contains`); no built-in heuristic guessing — there is no universal cross-widget "it worked" signal, and a heuristic that sometimes works is worse than an explicit contract |
| 3 | Retry / multi-attempt | **Not the tool's job.** One atomic attempt per call: grab → nav → drop → verify → release-on-failure. Retry and nav-strategy-switching stay in the caller's orchestration layer (same boundary `browser_click` already draws by not retrying against alternate selectors) |
| 4 | Resilience | **Poll the verify check** (interval + timeout, not a single immediate check — most DnD flakiness is post-drop animation/reflow settling); **three-boolean + staged result** (`grabbed`/`dropped`/`verified`, `stage_reached`) instead of one boolean, so the caller's retry logic has real signal instead of having to re-probe DOM state |
| 5 | "Hijackable" retries | **No code injection** — MCP tools take JSON over the wire, not Python callables, so the only pluggable-logic channel is a JS string evaluated in-page (already `verify_js`/`grabbed_predicate_js`). Configurability lives in parameters (`grab_key`/`drop_key`/`release_key`/`nav_key_sequence`), not callbacks |
| 6 | Error convention | **Deliberate deviation from `expect_*`.** `expect_selector`/`expect_js` raise `RuntimeError` on assertion failure — that convention would discard the structured result this tool exists to produce. `browser_a11y_dragdrop` always returns its result dict; it raises only for genuine infrastructure failures (selector never exists at all, frame detached) |

---

## 2. Goals & scope

### In scope (v1)

- `browser_a11y_dragdrop` MCP tool: keyboard grab → navigate → drop → poll-verify → release-on-failure, single atomic attempt.
- Three navigation modes: `tab` (Tab/Shift+Tab cycling), `arrow` (Up/Down/Left/Right), `keys` (explicit `nav_key_sequence`).
- Configurable grab/drop/release keys (default `Space`/`Space`/`Escape`, per WAI-ARIA APG convention — not hardcoded, since real widgets vary).
- Verification via required JS predicate or one of three convenience sugar shapes; poll-until-timeout, not single-shot.
- Structured result (`grabbed`, `dropped`, `verified`, `released`, `stage_reached`, `nav_steps_taken`) on every call — no exception on ordinary verify failure.
- Recording + macro replay support (new `a11y_dragdrop` action kind).
- Self-hosted test fixture (both APG variants) + headed manual-acceptance proof, mirroring how the screencast live-preview feature was verified.

### Out of scope (v1)

- Multi-attempt retry / nav-strategy-switching inside the tool (caller's job — see decision #3).
- Built-in heuristic success detection when no verify predicate is given (see decision #2).
- Mouse-based drag (already `browser_drag`).
- Touch/pointer-event drag variants.
- Auto-detecting which nav mode a widget wants (caller picks; no ARIA-role sniffing to guess it).

---

## 3. Architecture

Both `core_ops_mixin.py` (498 LOC) and `core_page_mixin.py` (548 LOC, already over the 500-line ceiling) have no headroom, so this is new files on both the session and server side — consistent with how other multi-concern features (goldens, upload_paths) already get their own module rather than growing a mixin.

```
src/octowright/session/a11y_dragdrop.py
    run_a11y_dragdrop(target, **params) -> dict   # pure engine: takes a Playwright Page/Frame-like
                                                    # target, returns the structured result. No
                                                    # recorder dependency — the mixin records the
                                                    # returned dict's inputs, this module only drives
                                                    # the browser and reports what happened.

src/octowright/session/core_ops_mixin.py
    async def a11y_dragdrop(self, ...) -> dict:    # thin delegate: calls run_a11y_dragdrop against
                                                    # self._target(), then self.recorder.record(...)

src/octowright/server/browser/a11y.py
    @mcp.tool browser_a11y_dragdrop(...)            # new file; input.py has no room for this tool's
                                                    # docstring plus WAI-ARIA APG explanation

src/octowright/macros/runtime.py
    _ACTION_MAP["a11y_dragdrop"] = "a11y_dragdrop"  # replay wiring; no rename/drop keys needed since
                                                    # recorded field names already match the method's

src/octowright/macros/lint.py
    required-field check: selector + exactly one of the four verify_* fields present
```

---

## 4. Parameter surface

```python
async def browser_a11y_dragdrop(
    instance_id: str,
    source_selector: str,
    nav_key: Literal["tab", "arrow", "keys"] = "tab",
    nav_direction: str | None = None,
    # "forward" | "backward" for nav_key="tab" (default "forward")
    # "up" | "down" | "left" | "right" for nav_key="arrow" (default "down")
    # ignored for nav_key="keys"
    nav_key_sequence: list[str] | None = None,   # required iff nav_key == "keys"
    max_nav_steps: int = 12,
    grab_key: str = "Space",
    drop_key: str = "Space",
    release_key: str = "Escape",
    grabbed_predicate_js: str | None = None,
    # default check (no override needed for most widgets):
    #   document.activeElement matches source_selector
    verify_js: str | None = None,
    verify_selector_appears: str | None = None,
    verify_selector_gone: str | None = None,
    verify_text_contains: str | None = None,
    # exactly one verify_* field is required — validated before any key is sent
    verify_timeout_ms: int = 2000,
    verify_poll_ms: int = 100,
    response_mode: str | None = None,
) -> dict[str, Any]
```

Validation, before any interaction with the page:
- `nav_key == "keys"` requires non-empty `nav_key_sequence`; any other `nav_key` rejects `nav_key_sequence` being set (ambiguous otherwise).
- Exactly one `verify_*` field must be set — zero is a footgun (silently "succeeds" without checking anything), more than one is ambiguous about which gates success.

## 5. Sequence

1. **Grab.** Focus `source_selector`, press `grab_key`. Evaluate `grabbed_predicate_js` (default: active element matches source). Fails → return immediately with `stage_reached="failed_grab"`, `released=False` — nothing entered grabbed state, nothing to clean up.
2. **Navigate.** Send up to `max_nav_steps` key presses per `nav_key`/`nav_direction`/`nav_key_sequence`. No mid-navigation target detection — that's what verify is for, after drop. Track `nav_steps_taken`.
3. **Drop.** Press `drop_key`. `dropped=True` records that the key was sent, not that anything succeeded.
4. **Verify.** Poll the configured check every `verify_poll_ms` up to `verify_timeout_ms`.
   - Success → `stage_reached="verified"`, `verified=True`, `released=False` (the drop keypress already exits grab mode per the APG pattern — no extra Escape needed).
   - Timeout → press `release_key`, `released=True`, `stage_reached="failed_verify"`, `verified=False`.
5. **Defensive cleanup.** Any unhandled exception after a successful grab presses `release_key` in a `finally` block before re-raising — mirrors the exact bug this tool generalizes ("if the grab succeeded but the drop failed, press Escape before trying the next strategy").

## 6. Return shape

```python
{
    "ok": bool,  # convenience alias for `verified`
    "grabbed": bool,
    "dropped": bool,
    "verified": bool,
    "released": bool,
    "stage_reached": Literal["failed_grab", "navigated", "dropped", "verified", "failed_verify"],
    "nav_steps_taken": int,
}
```

## 7. Error handling

Deliberately **not** the `expect_*` convention. `expect_selector`/`expect_js` raise on assertion failure because they exist to abort a script when a precondition isn't met. This tool exists specifically to let the *caller* decide what a failed verify means (retry, switch nav strategy, give up on this item) — raising would force every caller into try/except just to read `stage_reached`, defeating the point of decision #4. It raises only for infrastructure failures that make the result meaningless: `source_selector` matches zero elements, the frame/page is detached, or the underlying Playwright call itself times out for reasons unrelated to DnD semantics (these propagate as-is, same as `browser_click`).

## 8. Recording & macro replay

Recorded as a new JSONL action kind, field names matching the tool's parameters exactly (no `_REPLAY_RENAME_KEYS`/`_REPLAY_DROP_KEYS` entries needed — nothing computed gets recorded alongside the inputs, unlike `switch_frame` or `get_text_by`). Registered in `_ACTION_MAP` like `drag` and `press_key` already are. Not passive (`_REPLAY_PASSIVE`) or skipped (`_REPLAY_SKIP`) — it's a real user action with real intent, same tier as `drag`/`click`. `macros/lint.py` gains a required-field check mirroring the tool's own validation (selector + exactly one verify_* field), so a hand-edited macro can't silently ship a no-verify drag step.

## 9. Testing / "proven to work"

Three layers, same shape as the screencast live-preview feature's verification:

1. **Unit tests on the engine** (`tests/session/test_a11y_dragdrop.py`) against a `FakePage`/`FakeKeyboard` (deterministic, no browser) — covers all three `nav_key` modes, grab failure short-circuit, verify poll timing (fake clock, same pattern as the screencast `ScreencastViewer` tests), the release-on-failure and release-on-exception paths, and the "exactly one verify_* field" validation.
2. **Self-hosted fixture** at `tests/fixtures/a11y_dragdrop.html` — a small vanilla-JS page implementing both a Tab-cycle grid-cell pattern and an Arrow-key sortable-list pattern, so integration tests have no external-site dependency and run headless in CI. An integration test drives this fixture through the real tool (`browser_launch` + `browser_a11y_dragdrop`) for each nav mode.
3. **Headed manual-acceptance pass** against the same fixture — launch it, drive it, screenshot before/after — an actual browser actually doing the drag, not just green assertions.

---

## Self-Review

**Placeholder scan:** no TBD/TODO. ✓
**Internal consistency:** return shape (§6) matches the sequence's stage_reached values (§5) exactly; parameter surface (§4) matches architecture's tool signature (§3). ✓
**Scope check:** single feature, one new session module + one new tool module + two small existing-file edits (runtime.py, lint.py). Fits one implementation plan. ✓
**Ambiguity check:** "exactly one verify_* field" and the `nav_key`/`nav_key_sequence` pairing are both explicit validation rules, not left to interpretation. ✓
