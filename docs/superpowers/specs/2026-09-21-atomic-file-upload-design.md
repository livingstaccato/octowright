# Atomic File Upload

## Context

Octowright currently exposes `browser_set_input_files`, which assigns files
directly to a known `<input type=file>` element. During a live NotebookLM run,
an agent first clicked the visible **Upload files** button and then called
`browser_set_input_files` on the hidden input. The files uploaded successfully,
but the first click had already opened the native macOS file chooser. Directly
assigning the input did not dismiss that chooser, so it remained over the
browser and later upload clicks produced the system alert sound.

The recording proves this sequence repeatedly: each `click_by` action targeting
the upload button is followed by a successful `set_input_files` action and a
source-count increase. This is not a failed upload; it is an unmanaged native
chooser created by splitting one logical operation across two tools.

## Goals

- Add one atomic operation that captures a page's file chooser before clicking
  its upload trigger and supplies the files through that captured chooser.
- Support the same CSS and semantic trigger locators as `browser_click`.
- Record and replay the upload as one action so a macro cannot recreate the
  broken click-then-direct-assignment sequence.
- Preserve `browser_set_input_files` for direct, deterministic assignment to a
  known file input.
- Validate upload paths before any page interaction.
- Cover the MCP tool, session behavior, macro runtime, generated scripts,
  documentation, and a real browser lifecycle with automated tests.

## Non-goals

- Do not automate macOS, Windows, or Linux native picker UI.
- Do not attempt to dismiss a chooser that was opened before the atomic tool
  was called. A user must cancel an already-orphaned chooser, normally with
  Escape.
- Do not change ordinary `browser_click` into a stateful or file-aware action.
- Do not remove or change the behavior of `browser_set_input_files`.
- Do not weaken the existing upload-path allowlist.

## Public API

Add a new MCP tool:

```python
browser_upload_files(
    instance_id: str,
    paths: list[str],
    selector: str | None = None,
    role: str | None = None,
    role_name: str | None = None,
    role_exact: bool = False,
    label: str | None = None,
    label_exact: bool = False,
    text: str | None = None,
    text_exact: bool = False,
    test_id: str | None = None,
    timeout_ms: int | None = None,
    response_mode: str | None = None,
) -> dict[str, Any]
```

Exactly one trigger targeting mode is required: a CSS/XPath `selector`, or one
of `role`, `label`, `text`, and `test_id`. `role_name` and the `*_exact` fields
modify their corresponding semantic locator and do not count as independent
targeting modes. The matching behavior is the same as `browser_click`.

Example:

```python
browser_upload_files(
    instance_id="abc123",
    role="button",
    role_name="Upload files",
    role_exact=True,
    paths=["/Users/me/.config/octowright/uploads/report.pdf"],
)
```

`browser_upload_files` is the default when a site presents a visible upload
button, label, or drop-zone trigger. `browser_set_input_files` remains the
lower-level path when the actual `<input type=file>` is already identifiable;
it performs no click and is faster for that case. Callers must never click an
upload trigger before either operation.

## Behavior and Data Flow

1. The MCP handler rejects an empty/non-list `paths` value and invalid or
   ambiguous trigger locators before acquiring the browser operation.
2. The session method validates and resolves every path through the existing
   upload allowlist before clicking anything.
3. It resolves the trigger against the active frame: semantic locators use the
   existing locator builder, and selector locators use the active page/frame
   target.
4. It enters Playwright's `page.expect_file_chooser()` context before clicking
   the trigger. This ordering is the load-bearing invariant: Playwright owns
   the chooser event before the page asks the operating system to show it.
5. After the event resolves, it calls `FileChooser.set_files()` with the
   validated paths.
6. Only after successful assignment does it record one `upload_files` action
   containing the paths and trigger locator. It does not record an ordinary
   `click`/`click_by` action or a `set_input_files` action.
7. The handler optionally appends the same compact outline response supported
   by other interaction tools.

The session operation is gated as `browser_upload_files`. The supplied timeout
applies to locating/clicking the trigger and waiting for the chooser; the
existing default action timeout applies when it is omitted.

## Recording, Replay, and Export

Add `upload_files` as a first-class macro action. Its recorded fields mirror
the MCP inputs needed to repeat the atomic operation. Macro runtime dispatches
it to the session's atomic upload method.

Python, TypeScript, and standalone artifact exporters must render the same
ordering:

1. arm `expect_file_chooser` / `waitForEvent('filechooser')`;
2. click the recorded trigger locator;
3. await the chooser; and
4. set the recorded files.

Semantic locators and exact-match flags must survive recording and export.
Existing `set_input_files` recordings continue to replay and export exactly as
before.

## Errors and Safety

- A disallowed, missing, or non-file path fails before the trigger is clicked.
- An absent or ambiguous locator fails before any chooser wait begins.
- A trigger that does not emit a chooser raises the normal bounded Playwright
  timeout and produces no successful `upload_files` record.
- A chooser or file-assignment failure propagates through the existing browser
  operation error path and produces no successful action record.
- No raw path bypass is added: live calls, macro replay, and exported execution
  retain the existing allowlist expectations appropriate to their layer.
- The new tool does not promise to repair a native chooser already opened by a
  previous unmanaged click.

## Testing

Test-driven implementation will cover:

- rejection of empty paths and missing, multiple, or modifier-only locators;
- validation of every path before the trigger can be clicked;
- selector, role/name, label, text, and test-id trigger resolution;
- propagation of exact-match flags and explicit timeouts;
- listener-before-click ordering and chooser assignment;
- a single atomic recording with no standalone click or direct-input action;
- timeout and assignment failures producing no successful recording;
- MCP registration, argument forwarding, and outline response handling;
- macro lint/type acceptance and runtime replay;
- Python, TypeScript, and artifact-script output preserving atomic ordering;
- unchanged direct `browser_set_input_files` behavior; and
- a live browser page whose button opens a hidden file input, followed by a
  second page interaction and a clean browser close.

The live test demonstrates the complete Playwright lifecycle without depending
on a platform-native dialog implementation or an external site.

## Documentation and Release Integration

- Add the tool to the README and authoritative MCP inventory.
- Regenerate the tool-surface diagram and update its total tool count.
- Document the decision rule between atomic trigger upload and direct input
  assignment in both tool descriptions.
- Add the fix to the 0.25.0 changelog because it was discovered during release
  qualification and is required before the release branch is pushed.
- Re-run the full core, terminal, frontend, build, and packaging gates before
  creating the pull request.
