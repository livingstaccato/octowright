# Macros

A **macro** is a named, parameterizable action sequence derived from a recording.
Capture a flow once (e.g. a login), replay it later — possibly with different
arguments, possibly across many participants in a scenario.

Macros live as JSON under the Octowright config dir: POSIX uses the XDG config
dir `${XDG_CONFIG_HOME:-~/.config}/octowright/macros/`, and Windows uses
`%APPDATA%\octowright\macros\`. Override with `OCTOWRIGHT_MACROS_DIR`.

The macro tools (`macro_save`, `macro_run`, `macro_run_sequence`, `macro_list`,
`macro_delete`, `macro_lint`, `macro_repair_preview`, `macro_repair_apply`,
`macro_compile`, `macro_explain`, `macro_artifact_plan`, `macro_artifact_run`,
`macro_artifact_list`, `macro_digest`, and `macro_export_cli`) belong to the
`macros` capability profile. By default every tool registers; if your operator
runs `octowright serve --profile=core`, add `macros` to the spec
(`--profile=core,macros`) to keep these visible. See
[getting-started.md](getting-started.md#slimming-the-llm-tool-surface).

## Keep the origin out of the macro

A macro is the **behaviour**; the persona is the **where**. A macro that hardcodes
`https://app.example.com/login` can only ever run against that one deployment, so
proving the same flow on a local stack or a staging tier means a second copy of
the same behaviour that then drifts from the first.

Write the path and let the persona's `default_url` supply the origin — it becomes
the context's Playwright `base_url`:

```yaml
actions:
  - navigate: "/login"          # not "https://app.example.com/login"
  - expect_url: "/login"        # relative, same resolution
```

Now `browser_launch profile=buyer-local` and `profile=buyer-staging` replay the
identical macro against different origins. See
[personas.md](personas.md#host-relative-macros). Absolute URLs still work, which
is what a cross-origin step (an external identity provider, say) actually needs.

## When to use a macro

- **Login flows** that you want to re-run across multiple personas or sessions.
- **Setup steps** that put a page into a known state before exploration or testing.
- **Verification probes** tagged `[test]` that the test runner can execute as a suite.

When NOT to use a macro: durable production automation. Macros break when the
target site rewrites its DOM (Discord especially loves to rotate CSS classes).
Treat them as short-term automation — when one breaks, **re-record rather than
hand-patch**.

## Recording → save → replay

The canonical workflow:

```bash
# 1. Manually perform the flow on a live instance.
browser_launch kind=webkit profile=disc-1 url=https://discord.com/login
# ... fill email, password, submit ...

# 2. Snapshot those actions as a macro. Tell Octowright which literal values
#    should be parameters.
macro_save instance_id=<id> name=discord-login \
           parameters={"email":"me@octowright.test","password":"hunter2"}

# 3. Replay later, against any instance, with any args.
browser_launch kind=webkit profile=disc-2 url=https://discord.com/login
macro_run instance_id=<new-id> name=discord-login \
          args={"email":"other@octowright.test","password":"correcthorsebatterystaple"}
```

Lifecycle actions (`launch`, `close`, `snapshot`) are dropped by default — macros
are the **reusable middle** of a flow, not the wrapper. Pass `include_launch=True`
on `macro_save` if you need the initial navigation baked into the macro.

`OCTOWRIGHT_REDACT_INPUTS` (default `passwords`) records a password field as a
redaction marker, so the declared value never appears in the recording. The
rules for redacted fields do not affect a recording that has none. When there is
one, `macro_save` binds it to `{{name}}` only if exactly one field was redacted
and exactly one credential-named parameter matched nothing else in the
recording, as `password` does above. Otherwise it refuses before writing
anything: when more than one field was redacted, when more than one
credential-named parameter is left unmatched, or when none is left to fill the
field. Under `OCTOWRIGHT_REDACT_INPUTS=all` every `fill`, `fill_by` and
`type_text` value is redacted, so a recording with more than one of those is
always refused. Parameters that share a value are refused in every recording,
redacted field or not, because the recorded fields could belong to either; the
message names the parameters, never the value. Record such parameters with
distinct values, or re-record with `OCTOWRIGHT_REDACT_INPUTS=off` in a trusted
environment.

Recorded CSS `click` and `fill` actions may include semantic metadata such as
`role`, `role_name`, `label`, `text`, or `test_id`. Macro replay treats those as
ARIA-first hints: it tries `click_by` / `fill_by` with the semantic metadata,
then falls back to the recorded CSS selector if the semantic locator fails.
Standalone Python and TypeScript exports follow the same order.

## When a selector breaks

Octowright gives you four levels of selector resilience, cheapest first. Reach
for re-recording only when the earlier rungs don't apply.

1. **Runtime ARIA fallback (automatic, no action).** As above, replay already
   tries the stored `role`/`label`/`text`/`test_id` before the recorded CSS
   selector. A flow whose CSS broke but whose accessible name is unchanged keeps
   working with zero edits.
2. **`macro_repair_preview` (inspect).** Non-mutating. Returns, per selector-based
   action, the stored semantic replacement candidate (`click` → `click_by`) plus a
   review prompt. Nothing is written — use it to see *which* action indices are
   repairable before touching anything.
3. **`macro_repair_apply` (fix one action).** Applies the stored-heuristic
   replacement for a single `action_index`: it rewrites a brittle selector-based
   `click`/`fill` into its semantic `click_by`/`fill_by` form (from the
   role/label/text/test_id captured at record time), **drops the stale CSS
   selector**, and saves the macro in place. Raises — before writing — if the
   index is out of range or the action has no stored semantic locator. Typical
   loop: `macro_repair_preview` → pick an index → `macro_repair_apply` → `macro_run`.

   ```bash
   macro_repair_preview name=discord-login
   # action 3 → {"action": "click_by", "role_name": "Log In"}
   macro_repair_apply name=discord-login action_index=3
   ```

4. **Re-record (structural change).** When the UI moved, renamed, or restructured
   the flow itself — not just one selector — the semantic fields are stale too.
   Re-record the flow rather than hand-patching JSON. Macros are a disposable
   cache of a flow; the durable source of truth is the plan in your head (or a
   plain-language test note), not the JSON.

Designing for stable replay in the first place: prefer interactions that capture
a `test_id` (`data-testid`) or an accessible `role`+name, since those survive CSS
churn and feed rungs 1–3 directly.

> **Gotcha — prefer role over bare text.** Every launched page carries Octowright's
> translucent macro **status pill**, whose text echoes the current action
> description (e.g. `… | click_by text=Go`). A `click_by` / `fill_by` that targets
> by `text` alone can therefore hit a Playwright **strict-mode collision** — the
> pill's text matches too. Target by `role` + name (or `test_id`) so the locator
> resolves to the real control, not the overlay. `macro_repair_apply` carries
> whichever semantic fields were recorded, so capture `role`/`test_id` at record
> time to get clean repairs.

## Conditional / branching actions

For sites that ship multiple DOM versions of the same flow, four action types
let one macro cover all of them. Hand-author these by editing the JSON directly;
record the linear baseline first, then wrap the fragile steps.

### `if_selector`

Predicate on selector presence; runs `then` or `else`:

```json
{"action": "if_selector", "selector": ".cookie-banner", "present": true,
 "then": [{"action": "click", "selector": ".accept-cookies"}]}
```

### `try`

Best-effort sub-sequence that **suppresses errors**. Use for optional steps
like dismissing a one-off banner that may or may not exist:

```json
{"action": "try", "actions": [
    {"action": "click", "selector": "#optional-popup-close"}
]}
```

### `try_each`

Branches in order; succeeds on the first whose every action completes; raises
if all fail. The "v1 OR v2 OR v3" hammer:

```json
{"action": "try_each", "branches": [
    [{"action": "click", "selector": "[aria-label='Close']"}],
    [{"action": "click", "selector": "button.dismiss"}],
    [{"action": "press_key", "key": "Escape"}]
]}
```

These nest freely — `if_selector` inside `try_each` inside `try` works as you
would expect. See `examples/macros/conditional-discord-modal-dismiss.json` for a
real-world pattern.

### `macro_call`

Call another saved macro from inside a macro. This keeps large branches readable
and lets shared setup/dismissal snippets stay reusable:

```json
{"action": "macro_call", "name": "dismiss-cookie-banner",
 "args": {"variant": "compact"}}
```

Octowright detects direct and mutual recursion (`a -> b -> a`) and enforces a
depth cap so a bad macro graph fails with a clear error instead of looping.

## Network and absence assertions

Three actions check what a journey left behind rather than what it shows. They
exist only as macro steps; there is no `browser_*` tool for them.

### `expect_network_clean`

Fails when, inside its window, a request got no response (Playwright's
`requestfailed`) or the page threw an uncaught exception. A cancelled request is
not a failure: navigating away and an app's own `AbortController` both cancel
requests, reported as `net::ERR_ABORTED`, `NS_BINDING_ABORTED` or
`Load request cancelled` (`request_failures.ABORTED_REQUEST_FAILURES`).

```json
{"action": "expect_network_clean", "http_errors": true, "settle_timeout_ms": 5000}
```

- `since` -- `"run"` (default) judges the current macro run: each member of a
  `macro_run_sequence` is its own run, and a `macro_call` is part of its
  caller's. `"mark"` judges everything since the session's last
  `mark_network_clean` step, across runs, so a separate verify macro can judge
  the journey before it; without a mark it raises.
- `http_errors: true` also counts a 4xx/5xx page load, `fetch` or XHR. Images,
  fonts and favicons are left out, and the option is off by default because a
  4xx is sometimes the answer a journey expects.
- `settle_timeout_ms` -- how long to wait first for requests still in flight to
  end (default 5000; `0` judges at once). Event streams, websockets and media
  are not waited for. A request still pending when the wait ends is reported as
  `in_flight`, not failed, and one dropped from the bounded tracking (1000
  requests) as `in_flight_untracked`.
- `require_settled: true` fails the check in both of those cases instead of
  passing with a warning. It must be a real boolean; `"false"` is refused.

Requests are tracked in flight only while something will judge them: from the
start of a run that contains an `expect_network_clean` at any depth (through
conditionals and `macro_call`), or from a `mark_network_clean` step. The run
turns tracking off when it ends, unless a mark is open. The error carries counts
only, because a failed URL or an exception message can carry a credential; the
failure payload's `failed_requests` and `page_errors` name them, scrubbed.

### `mark_network_clean`

Starts the window `expect_network_clean(since="mark")` judges. A mark is never
closed, so after one the session keeps tracking requests in flight.

### `expect_no_text`

Fails when `text` is drawn anywhere under `selector` (default `body`, which
means every frame of the page when no frame is active). Drawn means text a
reader can see: rendered text, open shadow roots, visible form values and
placeholders, a broken image's alt text, a select's option labels and CSS
generated content. Password fields, attribute text such as a resource address,
`visibility: hidden` text and anything not rendered do not count, and neither
do octowright's own overlays. Case, whitespace and invisible characters are
ignored. On Chromium a whole-page check also reads the DOM snapshot, which
reaches closed shadow roots; other engines and exported scripts cannot. Canvas,
video and other pixel-only content cannot be text-checked.

```json
{"action": "expect_no_text", "text": "{{password}}", "selector": "#profile"}
```

- A selector that matches nothing passes, since nothing is drawn, but the
  result carries a warning. `require_match: true` fails it instead, `body`
  included. It must be a real boolean.
- `element_limit` (default 20000, or `OCTOWRIGHT_NO_TEXT_ELEMENT_LIMIT`) caps
  the elements read per frame. A scan that reaches it without finding the text
  **fails**: a security check does not pass on a page it only partly read.
  Narrow the selector or raise the limit.
- `timeout_ms` bounds each read (default: the action timeout).
- The text is treated as a secret. The error gives its length, never the text,
  and an empty `text` is refused because an empty string is in every page.

The recorder writes `<redacted:forbidden-text>` in place of the text, beside a
keyed digest of it. `macro_save` binds that marker to the one declared
parameter whose value has the same digest, credential-named or not. The key
lives only in the daemon's memory, so this works only for a recording made by
the daemon doing the save. Otherwise the marker stays, `macro_lint` reports it
(`redacted_assertion_text`), and replay refuses the step until its `text` is set.

### What a passing check saw

A check can pass on less than it was asked to judge: requests still pending, a
selector that matched nothing. `macro_run` therefore returns `assertions`, one
entry per `expect_network_clean` / `expect_no_text` step at any depth, and the
failure payload of a failed run carries the same list:

```json
{"assertions": [
  {"step": 3, "action": "expect_network_clean", "failed_requests": 0, "page_errors": 0,
   "in_flight": 1, "warning": "1 request(s) still in flight when the settle wait ended were not judged"},
  {"step": 5, "action": "expect_no_text", "selector": "#profile", "matched": 1,
   "frames_scanned": 1, "frames_skipped": 0, "truncated": false, "snapshot": "skipped"}
]}
```

`step` is the top-level step that was running. `snapshot` is `checked`,
`skipped` (the check was scoped to a selector or a frame) or `unsupported` (not
Chromium). The list is scrubbed of the run's sensitive values like the rest of
the result.

`macro_export_cli` runs all three steps, printing one JSON `assertion` line per
passing check and a `warning` line for a caveat; it watches popups and new tabs
as replay does, and has no DOM snapshot. `browser_export_script` (Python and
TypeScript) cannot run them, since a linear script keeps none of the state they
need, and emits a step that raises rather than dropping the check.

## YAML DSL

JSON remains the runtime/storage format, but `macro_compile` can compile a
friendlier YAML document into canonical macro JSON. Dry-run first:

```bash
macro_compile yaml_text='
name: login-smoke
parameters: [email, password]
actions:
  - navigate: "https://octowright.com/login"
  - fill: {selector: "#email", value: "{{email}}"}
  - fill: {selector: "#password", value: "{{password}}"}
  - try_each:
      branches:
        - [{click: "button[type=submit]"}]
        - [{press_key: Enter}]
' write=false
```

Pass `write=true` to save the compiled JSON under the normal macro directory.
The dashboard editor also edits the canonical JSON shape and shows branch
summaries for conditionals.

## Argument privacy

A macro argument whose **name** reads like a credential, identity, or context is
classified when the macro runs. Classification is by name, not by value.
Credential names include `password`,
`passwd`, `pwd`, `pw`, `passphrase`, `secret`, `token`, `api_key`, `access_key`,
`private_key`, `authorization`, `auth`, `bearer`, `cookie`, `otp` and
`credential`, including plural and camelCase spellings. Identity and contextual
names such as `email`, `username`, `user`, `peer` and `session` are classified
too. A value nested under a classified key inside an ordinary container
(`{"profile": {"password": ...}}`) is classified as well.

**Structural redaction and blind scrubbing are different.** Every classified
argument is redacted where its key provides provenance, so `args_used`,
manifests and argument dictionaries never expose values stored under `email`,
`user`, `password` or another classified key. Blind scrubbing has no such
provenance: it replaces an admitted value wherever that text occurs in a
diagnostic, recording row, artifact or page prepared for a screenshot. The
default admits only credential-tier values. See **Blind-scrub policy** below.

**Screenshots of a blind-scrub-protected run.** A screenshot of a page a
credential was typed into is a durable copy of it, which no text scrub can
reach. While a run holds values admitted by the blind-scrub policy, a
`screenshot` action is never taken the generic way. It is decided in this
order:

1. If the embedding application called
   `octowright.macros.safe_screenshot.enable_redacted_screenshots(session, handler=...)`
   with its own handler, that handler decides, and may call `redacted_screenshot`
   itself.
2. If it called `enable_redacted_screenshots(session)` without a handler, or
   `OCTOWRIGHT_MACRO_CLASSIFIED_SCREENSHOTS=redact` is set (see
   [env-vars.md](env-vars.md)), octowright takes a **redacted screenshot** of a
   Chromium page:
   - It pauses the page's animations for the whole capture and ends every running view
     transition, on the document or on any element in it or in its open or closed
     shadow roots, which would otherwise draw its raster of the old state from before
     the redaction, and waits, reading DevTools rather than the page, until their
     pseudo-elements are gone.
   - It replaces every raw, JSON-escaped and URL-encoded spelling of the run's
     policy-admitted values with `<redacted>` in text and attribute values across the
     document and its open and closed shadow roots. Case, whitespace, Unicode
     compatibility forms and invisible characters inside a value (zero-width spaces,
     soft hyphens, joiners, bidi controls, control characters) are ignored. A value
     made only of digits and number punctuation, such as a phone number, is also
     matched by its digits, formatted differently or by an ending of at least seven
     digits. A string that still holds a value after replacement is replaced whole.
   - A text control holding a value keeps its value, caret and selection; its text is
     masked with `-webkit-text-security` instead. Every other input, such as a button,
     hidden input, checkbox, radio, or color, date, time or range input, has its value
     replaced; a file input whose chosen file name holds a value refuses.
   - It hides `canvas`, `video`, `embed`, `object`, `frame` and `iframe` elements,
     whose pixels it cannot read, and
     any element whose `src`, `srcset`, `srcdoc`, `data` or `poster`, or whose link as
     an SVG image, `use` or filter image, holds a value (for a `<picture>` source, the
     picture's image). An SVG image or `use` that an `<animate>` or `<set>` targets
     through its link is hidden too, whether or not the animation has begun, because
     an animated link is drawn without being the attribute or changing the page; a
     `use` whose link animation only names fragments of the same document is left
     visible, because it draws page content the redaction already covers; a filter
     image with an animated link refuses. Elements and attributes are judged by their
     local names, so a namespace prefix (`svg:image`, `x:canvas`, `q:href`) changes
     nothing; an animation's `attributeName`, `to`, `from`, `by` and `values` are read
     without a namespace, as Chrome reads them. `to`, `from` and `by` are judged as
     written, so a blank or space-led link counts as another document; each `values` item
     has its ASCII whitespace stripped and an empty item is skipped. Only a link starting
     with `#` names this document. Hidden elements get `visibility: hidden` and `opacity: 0`, which content
     a `use` draws cannot undo, and lose their transitions, so they vanish at once.
     A resource address is never rewritten, because a rewritten frame address would
     navigate or reload.
   - It has Chrome apply the page's pending style changes through DevTools, so a stylesheet
     its own redaction rewrote is replaced before counting begins, not counted as a change.
     If Chrome cannot apply them, because the page replaced its document, say, the screenshot
     is refused.
   - From then on it counts the page's changes. Chrome reports every DOM mutation
     (closed shadow roots and same-process frames included), every stylesheet added,
     removed or edited through any CSSOM route, and every new animation. The page
     controller counts changes Chrome does not report: an inline style change on an
     element the redaction styled or holding a value (any inline style change, if a
     stylesheet holds a value), checked, indeterminate, selected and validity state,
     focus, and the location hash. Inline style changes that could reveal nothing,
     such as a spinner's transform, do not count.
   - Before and after the capture, it refuses the screenshot and deletes any file if
     it counted a change, if a view transition is running anywhere it redacted or Chrome
     still draws one in any root (including a shadow root attached after the redaction
     collected its roots), if the redacted page still
     holds a value, or if Chrome's rendered surface does. The rendered surface is read with
     `DOMSnapshot.captureSnapshot`: layout text and text boxes (generated content and
     same-process frames included), drawn form values that are not masked, drawn
     attributes (`placeholder`, `alt`, `label`), resource addresses and image styles
     (`content: url()` included). Its text is matched joined, reversed, in visual
     order, and with up to a few unrelated text boxes between the parts of a value.
   - It captures through the same DevTools session (`Page.captureScreenshot`), not
     Playwright's screenshot helper, which writes styles onto the page first.
   - It then restores the page. A text, control value, attribute or style property is
     written back only while it still holds what the redaction left, so a value the page
     changed meanwhile stays, and so do the page's other style changes. Hidden elements
     come back with their transitions off until their style has settled, so a transition
     of their own does not replay, even when their style attribute was also redacted, and
     neither does one the page's own style change started from the hidden state (removing
     the style attribute, say). Attributes are written back through their attribute nodes.
     A style attribute that held a value comes back whole if the page changed it in place,
     because that change cannot be told apart from the redaction's text, and one redacted
     before its element was hidden comes back whole even if the page removed or replaced
     it. An attribute node the page moved to another element is restored there, unless the
     page changed its value. Any other attribute the page removed stays removed, unless the
     page put back exactly what the redaction wrote under the same namespace and local
     name, which then gets the original value. If the restore fails or is cancelled, the
     screenshot is deleted.
3. Otherwise the screenshot is refused.

The in-page state is held through octowright's own DevTools session, not on a page
global, so page script cannot reach it.

The limits are real and deliberate:
- The page is assumed to be the application under test, not an adversary. Page script
  keeps running during the capture. A script that kept its own references to the
  form-state setters the controller counts can change that state without being counted.
- Text matching covers the spellings and arrangements listed above, not every way a
  page could draw a value: for example one glyph per absolutely positioned element
  scattered across the page, a value only partly reversed by a bidi override, or a
  number displayed in words.
- A masked control still shows how many characters its value has.
- The page's own mutation observers see the redaction while it lasts, so an application
  that saves what it observes, such as an autosave, could save `<redacted>`.
- Pixels of an ordinary image are not read; an image is hidden only when one of its
  resource addresses holds a value.
- Firefox and WebKit have no rendered-surface snapshot, so a redacted screenshot is
  refused there.

**Why a screenshot was refused.** A refusal says what kind of surface held a
classified value, the value's tier, and the argument it came from -- never the
value, the page text around it, or a selector built from it. The same fields
appear in three places: the failing step's error (so `octowright test`, with or
without `--redact-errors`, and `macro_run`'s failure line name them), the failure
payload's `screenshot_refused` field, and one `octowright.macro.screenshot_refused`
warning in the log. For the field report that prompted this -- a sequence signs in
with `username` as a credential argument, the app's header renders it as `Admin`,
and the next step's `screenshot` is refused -- the `--redact-errors` line reads:

```
macro bmf-screenshot failed at step 1 (screenshot): screenshot refused (rendered text; tier credential; argument username)
```

and the payload carries
`"screenshot_refused": {"reasons": ["rendered text"], "stage": "before", "tiers": ["credential"], "args": ["username"]}`.

- `reasons` are the kinds the rendered-surface scan already tells apart:
  `rendered text` (layout text or text boxes, in any of the arrangements above),
  `form value` (a drawn, unmasked control value), `visible attribute text`
  (`placeholder`, `alt`, `label`), `option text` (a `<select>` option's label),
  `visible resource address` (a `src`, `srcset`, link and the like), `visible style
  image` (an image style or `content: url()`), and `visible canvas`, `visible video`
  and the like (an element whose pixels cannot be read, refused whatever it shows). A
  refusal before or beside that scan names its own cause instead: `page changed`,
  `value left in the page` (the redaction could not remove one), `view transition`,
  `styles not applied`, `no rendered-surface snapshot` (not Chromium), `handler
  refused`, or `no privacy handler` (the default `refuse` policy).
- `stage` is `before` or `after` the capture, when the refusal has one.
- `tiers` (`credential`, `identity`, `contextual`, strongest first) and `args` (the
  argument path, as `scrub_exempt_args` reports it) come from where the session
  ledger admitted each value. For a rendered-surface refusal they name the values
  the page was found drawing; for any other refusal, every value the session holds,
  which is why the screenshot was classified at all. A password typed into a
  password field is `credential` with no argument, and a value with no recorded
  origin (a direct `redacted_screenshot` call) adds nothing rather than a guess. An
  argument path that itself spells a held value is shown as `<redacted>`.

The refusal is correct by design: the page drew the value, so the pixels would
hold it. The ways out are to keep the value off the screen (sign in as a user whose
name the page does not show, or screenshot a page without it) or to stop
classifying it. Declaring an argument not sensitive is not available yet: that is
the open Part B (`parameter_specs`) of #248, and this reporting adds no
declassify mechanism of its own.

Automatic artifact screenshots follow the same rule, with one exception: they are
never taken on a session whose application installed its own handler. A mistyped
policy value suppresses them rather than failing the artifact run. When one is not
taken, the evidence manifest records `screenshot_suppressed`. The generic diagnostic
producer, which saves raw page HTML and a screenshot, is not called for a failed run
when the run or the session ledger (below) holds any value; the payload records
`diagnostic_suppressed` instead.

**Screenshots outside a protected run.** Once the session ledger holds a value, every
other screenshot of that session -- `browser_screenshot`, `browser_each`,
`browser_capture_and_close`, and a macro `screenshot` step of a run that holds no
values itself -- goes through the same boundary (`safe_screenshot.ledger_screenshot`):
an installed handler decides, otherwise a Chromium page is redacted and proved as
above, and Firefox and WebKit refuse and write no file. This is
`OCTOWRIGHT_LEDGER_SCREENSHOTS=refuse`, the default, and it does not consult
`OCTOWRIGHT_MACRO_CLASSIFIED_SCREENSHOTS`. `allow` takes raw screenshots again,
unless the application installed a handler; it is the way back to screenshots
after a login on Firefox or WebKit that keeps the recordings redacted.

**Nested calls and later runs.** A `macro_call`'s own arguments are classified
where the call executes, at every depth. Values admitted by the selected policy
join the run ledger; credential-tier values also join the session ledger, and
identity/contextual ones (admitted only under `all`) join it only for the run
that supplied them -- see **Short and common values** below. A value typed by any fill or
type, `browser_fill` and `browser_type` included, into a field classified as a
password (`type=password`, or `autocomplete` `current-password`, `new-password` or
`one-time-code`), unless `OCTOWRIGHT_REDACT_INPUTS=off`. The session ledger lasts for the session's lifetime,
including the next step of a `macro_run_sequence`, because a credential typed
once can keep rendering in later page output. It scrubs every later recording
row, the live console, network and page-error buffers the inspection tools
read, the websocket frame sidecar (a binary frame holding a value is not stored,
and says `payload_redacted`), the markdown page cache and `capture_create`
captures. A macro's own values are scrubbed wherever they appear (one shorter
than four characters only on a word boundary); a typed password is scrubbed only
where it stands as a whole identifier, because it is
often an ordinary word (`admin`) and replacing it inside `#admin-menu` broke the
selectors of a macro saved from the recording.

A failure payload goes back to the MCP client, and its fields follow two rules.
The text the page produced -- `original` (the exception), the console tail,
`failed_requests`, `page_errors`, the `assertions` block, the A11y
tree inside `healing_suggestion`, and the error of any producer that failed --
is scrubbed of the run's and the session's values by the first rule, a typed
password included, so an echo glued to other characters (`hunter2-reset`) does
not reach it. The fields that echo the macro itself -- `failed_action`,
`executed_actions`, and the step `healing_suggestion` names -- show each step as
the macro wrote it, before substitution, and are not scrubbed of those values:
a placeholder stays `{{order}}` rather than showing what it expanded to, and a
typed `admin` does not rewrite the macro's own `#admin-menu`. Two structural
redactions still apply there: a `fill`, `type` or `expect_no_text` value is
`<redacted>`, and a `macro_call`'s arguments are redacted as `args_used` is.

**Blind-scrub policy.** `OCTOWRIGHT_MACRO_BLIND_SCRUB_POLICY` selects one of
three strict modes:

- `credentials` (default) admits only passwords, tokens, cookies, API keys and
  other credential-tier values to blind scrubbers and the permanent session
  ledger. Identity/context values remain structurally redacted, but a value
  such as `session="1"` does not rewrite `li:nth-child(1)` or `?page=1`, and
  `user="admin"` does not rewrite unrelated prose.
- `all` admits every classified tier, except identity/contextual values that
  are short or common (below). It offers more identity/context redaction, at
  the cost that a value it does admit can still rewrite a selector, URL or
  text that happens to contain it, in the rows its own run writes.
- `reject` refuses an invocation containing identity or contextual values
  before substitution, recording changes, artifact writes, screenshots or
  browser activity. Its error lists argument paths and tiers, never values.
  Credential-only invocations continue normally.

Unknown values fail configuration rather than silently choosing a privacy
posture. This setting also governs diagnostics, automatic and explicit
screenshots, artifact reports and generated scripts. It does not change the
classifier vocabulary or the credential sink guard.

**Short and common values** (#247). A blind scrub has no provenance, so under
`all` an identity or contextual value such as `session="1"` rewrote
`li:nth-child(1)` and `?page=1`, and `user="admin"` rewrote the word "admin" --
in every later row on the session, because the session ledger is never cleared.
Two rules now bound that, and neither ever applies to a credential-tier value
(a credential-named argument, or one that is credential-tier by position, such
as an `expect_no_text` text), which is still scrubbed at any length and for the
session's lifetime, so a short password is never left in cleartext:

- **Too short or too common to scrub.** An identity/contextual value shorter
  than `OCTOWRIGHT_MACRO_SCRUB_MIN_LENGTH` (default 4: below that a value is a
  small number, an initial or a two-letter code, exactly the strings selectors,
  query strings and prose are made of) or on the common-value list
  (`OCTOWRIGHT_MACRO_SCRUB_COMMON_VALUES`; by default `admin`,
  `administrator`, `anonymous`, `default`, `demo`, `example`, `guest`,
  `none`, `null`, `root`, `test`, `tester`, `user`, compared ignoring case and
  surrounding space) is not blind-scrubbed. It is still structurally redacted
  from `args_used` and every other record of the arguments. The run result
  says so: `scrub_exempt_args: [{macro, path, tier, reason}]`, with `reason`
  `short` or `common` and the argument's path -- never its value -- for the
  run's own arguments and every nested call's. It is present only when
  something was exempted, and also appears on a failure payload and on a
  `macro_artifact_run` result.
- **Scrubbed for its own run only.** An identity/contextual value that is
  admitted is scrubbed from the rows, buffers, captures and screenshots of the
  run that supplied it (nested calls included), and dropped from the session
  ledger when that run ends, pass or fail. The next `macro_run_sequence` step
  or a later run does not rewrite it. The cost: a page that keeps echoing the
  value after the run ends records it in clear, which is acceptable for a value
  that is not a secret. Set `OCTOWRIGHT_MACRO_SCRUB_RUN_SCOPED=off` to keep
  such values session-wide, as credentials are.

Every unparsable value for these three knobs falls back to the side that
scrubs more (no floor, session-wide); see [env-vars.md](env-vars.md). Under the
default `credentials` policy none of this applies, because no identity/contextual
value is blind-scrubbed at all.

**A full scrub set** (#248). The session ledger is append-only and never
cleared, and every recorder write, console message, network row and socket URL
is scrubbed against it, so its cost grows with every distinct value it holds.
`OCTOWRIGHT_MACRO_SCRUB_MAX_VALUES` (default 256, `0` for no cap) bounds the
session-wide values -- credentials, and passwords typed into a password field
-- but never by dropping one: privacy wins over cost, so every value that
reaches the ledger is still added and scrubbed. Run-scoped values are not
counted. Once the count reaches the cap the session is `scrub_saturated` for
its lifetime (logged once as `octowright.macro.scrub_saturated`, with the
count and cap), and the only enforcement is a refusal: a `macro_run`, sequence
step, `macro_artifact_run` or nested `macro_call` that would add a value the
ledger does not already hold is refused with an invalid-request error before
anything runs or is written (an artifact run creates no run directory). The
message gives the count and cap, never the value; relaunch the browser for a
fresh session or raise the cap. A run whose values are all already held runs
normally, a refused sequence step is a failed step, and a refused nested call
fails the step that made it. A direct `browser_fill` into a password field is
never refused -- the text is already typed -- so it can still take the session
over the cap. Run results, failure payloads and `macro_artifact_run` results
carry `scrub_saturated: true` on a saturated session, and omit it otherwise.

**Credential-named arguments in URLs, code and outbound fields.** A
credential-named argument expanded into `url`, `expression`, `verify_js`,
`grabbed_predicate_js`, a `headers` value, a mock_route `body` or an upload
`paths` entry is refused by default. The one exemption is a header sent to the
session's own origin: `inject_headers` whose `pattern` spells out the scheme,
host and port of the launch URL or persona `base_url` may carry
`Bearer {{token}}` (`https://app.example.test/**` for a launch at
`https://app.example.test`, but not `http://localhost:45678/**` for a launch at
`http://localhost:3000`). See `OCTOWRIGHT_MACRO_CREDENTIAL_SINKS` in
[env-vars.md](env-vars.md) for the full name list, match rules and opt-out.

**Credentials are typed only onto the session's own origin.** A `fill`,
`fill_by` or `type` whose value comes from a credential-named argument checks,
immediately before it runs, the origin of the page (or active frame) it would
type into. On the launch URL's or persona `base_url`'s origin it runs; anywhere
else it is refused, naming the origin -- a shared macro that navigates to
`https://evil.example/login` and then fills `{{password}}` would otherwise hand
the password to that page's JavaScript. A sign-in hop to an identity provider
lists that origin on the step itself, literally:

```json
{"action": "fill", "selector": "#password", "value": "{{password}}",
 "allowed_origins": ["https://login.idp.example"]}
```

Entries are exact origins (`scheme://host[:port]`): no wildcard, path or
`{{placeholder}}`, which `macro_lint` reports and replay refuses. Identity and
contextual arguments (`{{email}}`, `{{username}}`) are not checked.
`OCTOWRIGHT_MACRO_CREDENTIAL_FILL_ORIGINS=warn` logs and runs the step instead,
recording `credential_fill_offsite: [{step, action, origin}]` in the run result,
and in the failure payload when a later step fails;
`OCTOWRIGHT_MACRO_CREDENTIAL_SINKS=allow` turns this and every other credential
check off.

The origin checked is that of the document that receives the value, at the
moment it receives it. A `fill` / `fill_by` picks its element as it would
without a credential (a `fill` selector's first match; a `fill_by` locator
strictly, so a label that also matches "Confirm password" is Playwright's
strict-mode error), checks the frame that owns it and fills that element; if
the element is replaced (a re-render, or a navigation during the fill's wait)
it resolves the selector again and re-checks, so a hydrated form is still
filled and a page that moved to another origin is refused. A `type` goes one
key at a time and, before each key, checks the document that has focus. Keys
follow focus within one document like a keyboard's (an auto-advancing
one-time-code form works), and the rest of the value is stopped, failing the step with an
error that names the step and never the value, when:

- the document with focus is not the one that received the previous key --
  any navigation, same-origin included (a sign-in that lands on a dashboard
  whose search box takes focus), or focus moving into another frame. A
  foreign document is refused naming its origin;
- focus moved to `<body>` or nothing after an element took the first key,
  which is where it falls when the field is re-rendered away, so a truncated
  value is never reported as typed. The first key itself may go to `<body>`
  of the checked document: a target that cannot take focus (a canvas console,
  a `div` with no `tabindex`) leaves focus there and reads keys from the
  document, and the same step without a credential types there too. Later
  keys then need focus to stay on `<body>` of that document;
- focus moved to an element that takes no text (a button, a link) other than
  the one the first key went to.

A selector that enters a frame (`iframe >> internal:control=enter-frame >> #pw`)
is checked on that frame. What is left: the single driver round trip inside
one Playwright fill or keypress, between focusing the element and dispatching
the input; and focus the page moves to another text field of the *same*
document, which is followed by design, so a single-page app that swaps its
view in place (`history.pushState`) and focuses a search box receives the rest
of the value; and a document the back/forward cache restores during the step,
which is the same document as before. The whole step -- retries and every key --
is bounded by one budget: a `fill`/`fill_by` step's `timeout_ms`, else the
action timeout (`OCTOWRIGHT_ACTION_TIMEOUT_MS`, default 15000). A `type` gets
the action timeout **plus** `len(text) * delay_ms`, in both key modes: the
pauses the step asked for are not the page being slow, so they are not counted
against it. That is deliberately more than a `type` without a credential in
the default text mode gets: Playwright's `page.type(delay=, timeout=)` counts
the pauses, and fails partway once the typing outlasts the timeout (measured
on all three engines: ten keys 100ms apart under a 300ms timeout stop after 2-3
keys; twenty under 1500ms after 13-15). A `key_mode: keys` type without a
credential has no whole-step bound at all. Each Playwright call is given what
is left of the budget, and the step's own backstop fires one second after it,
so a selector that never matches fails with Playwright's own "waiting for
locator(...)" error rather than as a step that stopped. Before any of that, every
`fill`/`fill_by`/`type` step -- with a credential or without -- waits once for
its element to be attached, under the whole budget, then reads the element's
role and redaction class against the attached element and gives the action
what is left. So a target that never appears fails in about its budget (measured
within a few milliseconds of it on all three engines) with Playwright's
`Locator.wait_for: Timeout <budget>ms exceeded ... waiting for locator(...)`,
rather than spending the budget on the metadata read and a second one on the
action. A step the backstop
does stop says whether anything was typed: "did not start typing ... Nothing
was typed" when the page never answered before the first key, "stopped typing
... The rest of the value was not typed" otherwise. An exported script's
credential step waits what its step without a credential does when the step
names no `timeout_ms`: Playwright's own 30s default.

A credential passed to a called macro under another name
(`macro_call` `args: {q: "{{password}}"}`) is a credential in the callee too.

A step whose `action` is itself a placeholder (`"action": "{{kind}}"`) is
judged as the action it expands to, so `kind=fill` is checked as a credential
fill and `"{{call}}"` resolving to `macro_call` keeps its credential taint. A
credential-named argument is refused as an action name.

**Exported scripts enforce the live guards.** A script from `macro_export_cli`
refuses a credential in a URL, code or outbound field, types a credential only
on an origin passed as `--trusted-origin` (or listed in the step's
`allowed_origins`), and uploads only from the upload staging directory or
`OCTOWRIGHT_UPLOAD_ROOTS` -- the same rules `macro_run` applies, rendered from
the same source.

**Exported scripts** carry their own copy of the classifier, stamped
`_ARG_PRIVACY_CLASSIFIER_VERSION = 6`, and resolve the same blind-scrub policy,
length floor and common-value list when they run (a script is one run, so run
scoping does not arise there). A script exported by an older octowright keeps the classifier
and policy behavior it was generated with; regenerate it to pick up the current
default.

**Not covered.** These writers are not scrubbed:

- the page HTML and screenshot the generic diagnostic producer saves when a run
  fails while neither it nor the session ledger holds a value -- which can still
  show a secret the ledger never learned, such as one typed with
  `OCTOWRIGHT_REDACT_INPUTS=off` or into a field not classified as a password;
- a HAR file, when HAR recording is enabled at launch;
- a Playwright trace, when `browser_launch` is called with `trace=true`, saved
  beside the recording as `.trace.zip` with each action's arguments, including
  typed values, alongside DOM snapshots and screenshots;
- a launch video, when `browser_launch` is called with `record_video=true`,
  which shows whatever a field displays while it is filled.

## Linting

Before promoting a macro into shared workflows or CI, run:

```bash
macro_lint name=discord-login
```

The linter catches:

- Missing required fields on each action.
- Unknown action types (typos).
- **Unparameterized credential-shaped strings** (looks like a password or token
  but isn't a `{{parameter}}`) — the most common security mistake.
- Empty conditional branches that would silently no-op.
- An `allowed_origins` entry replay would refuse (`bad_allowed_origins`): a
  wildcard, path or `{{placeholder}}` instead of an exact origin.
- An `expect_no_text` whose text is still the recording's redaction marker
  (`redacted_assertion_text`), which replay refuses.

## Test suite mode

Macros tagged `[test]` (in either `name` or `description`) participate in the
test runner. The runner emits JUnit XML so the result drops cleanly into any CI
reporting pipeline.

```bash
uv run octowright test [path] --kind webkit --tag smoke --out "$OCTOWRIGHT_RECORDINGS/ci/macro-tests.xml"
```

Equivalent MCP tool: `run_test_suite`.

**Where the report goes.** `--out` must resolve under `OCTOWRIGHT_RECORDINGS`,
like every other path octowright writes; its directory is created if it does
not exist. Without `--out` the report is `<artifacts>/octowright-report.xml`
when `--artifacts` is given, else a timestamped
`octowright-report-<UTC stamp>.xml` directly under `OCTOWRIGHT_RECORDINGS`.
The path is checked **before** anything launches, so a refused one (say
`--out dist/x.xml` from a checkout) fails at once with
`test run refused: suite report path ... resolves outside ...` and no browser.
To keep the report in a CI workspace, point `OCTOWRIGHT_RECORDINGS` there, or
copy the file out after the run from the printed `report:` line.

`--persona <name>` launches each test browser as that persona, so it gets the
persona's profile, `default_url`, trusted roots and credentials. Every test
would open the persona's one persistent profile, and a second browser on a
profile that is already open fails (Chromium's `SingletonLock`), so a persona
runs its tests one at a time: `--persona` with `--max-parallel` above 1 is
refused as a usage error.
`--redact-errors` records a failure as macro, step and action only, never
exception text, for runs whose reports are kept as evidence. That covers a
browser that fails to close, too. A refused classified screenshot also keeps
its value-free reason (see "Why a screenshot was refused" above).

A browser that fails to **close** does not fail a passing test: it is carried as
the test's `teardown_warning` (appended to the error of a test that had already
failed), and the report is still written -- for a sequence as for a suite, on
the last step that ran.

### Sequences

A sequence runs several small macros, in order, in **one** browser. It keeps
each macro to one piece of behaviour while a check walks them in sequence:

```json
[
  {"macro": "app-login", "args": {"username": {"credential": "username"},
                                  "password": {"credential": "password"},
                                  "screenshot": {"artifact": "signed-in.png"}}},
  {"macro": "orders-page-healthy", "args": {"route": "/orders", "password": {"credential": "password"}}}
]
```

```bash
octowright test --kind chromium --persona buyer --sequence sequences/smoke.json \
    --artifacts "$OCTOWRIGHT_RECORDINGS/smoke" --redact-errors
```

- `{"credential": name}` is resolved from the persona at run time, so no
  secret is ever written into the sequence file. A credential the persona
  cannot supply fails the run before any browser launches. The argument stays
  a **credential whatever the macro calls it**: `{"pin": {"credential": "pin"}}`
  is scrubbed from the recording and failure text, redacted in `args_used`,
  and held to the credential sink and fill-origin guards, though `pin` is not
  a name the classifier would recognise on its own.
- `{"artifact": file}` becomes a path under `--artifacts`, which must sit
  under `OCTOWRIGHT_RECORDINGS`. The JUnit report is written there as
  `octowright-report.xml` unless `--out` names another path (see
  [where the report goes](#test-suite-mode)).
- The sequence stops at the first failing macro; later steps are reported as
  skipped. One JUnit testcase per step. `--sequence` and `--tag` are exclusive.

### Recording a video of the run

`--record-video` records the test browser (off by default; without it nothing
about the run or its output changes):

```bash
octowright test --kind chromium --persona buyer --sequence sequences/smoke.json \
    --artifacts "$OCTOWRIGHT_RECORDINGS/smoke" --redact-errors --record-video
```

```
1/2 passed
report: /…/smoke/octowright-report.xml
video: /…/smoke/smoke.webm
```

- The video is read only after the browser has closed, through Playwright's
  own `Video.path()`: a page's `.webm` exists from the start but stays empty
  until its context closes, so it is never copied early.
- With `--artifacts` it is copied there as `<sequence-stem>.webm`. A page the
  run opened later (a popup, a new tab) records its own video, copied as
  `<stem>-2.webm`, `<stem>-3.webm`, … numbered by the order the pages opened
  (a page whose video could not be read keeps its number free). Each
  `video:` line follows the `report:` line, in that same order. Copies are
  written `0600`, like the session recording.
- **Nothing is overwritten.** A name already taken -- by another test or page
  of the run, or by a file already in the directory (compared
  case-insensitively, as macOS and Windows file systems do) -- gets `_2`,
  `_3`, … before `.webm`: re-running into the same `--artifacts` gives
  `smoke_2.webm`, and macros `a b` / `a_b` or `Login` / `login` get two files.
  The `video:` lines name what was actually written.
- A video still **empty** after the browser closed is saved once more through
  Playwright's `Video.save_as` (which waits for the file); if it is still empty
  it is left out and logged as `octowright.runner.video_empty`.
- Without `--artifacts` the video stays where Playwright wrote it, in the
  launch's directory under `$OCTOWRIGHT_RECORDINGS/videos/`, and that path is
  printed.
- A **failed** run keeps its video and prints its path -- including a run that
  raised, was interrupted with Ctrl-C, or whose browser failed to close
  cleanly. That is usually the video you want.
- `[test]` suites (no `--sequence`) record one video per test, copied as
  `<macro>.webm` when `--artifacts` is given (a macro name is reduced to a safe
  file name first). With `--max-parallel` above 1 the `video:` lines come in
  the order tests finished.
- `--redact-errors` works as before and the `video:` line carries only a path,
  but the video itself is not redacted: it shows whatever the page displayed.
  Treat it with the same care as the session recording.
- **Size.** `octowright test` launches at `OCTOWRIGHT_VIEWPORT_W` x
  `OCTOWRIGHT_VIEWPORT_H` (default 1280x800, so unset nothing changes; a value
  that is not a positive integer, such as `1920px` or `0`, falls back to the
  default with a logged warning), and
  the video is recorded at exactly that size -- octowright pins Playwright's
  `record_video_size` to the viewport, since Playwright's own default scales
  the video down to fit 800x800 (a 1920x1080 page otherwise records at
  800x450, measured on all three engines):

  ```bash
  OCTOWRIGHT_VIEWPORT_W=1920 OCTOWRIGHT_VIEWPORT_H=1080 \
      octowright test --sequence sequences/smoke.json --record-video
  ```

- **Pace.** `OCTOWRIGHT_MACRO_SLOWMO_MS=<ms>` applies to every macro
  `octowright test` runs, suites and sequences alike: it waits that long
  before each action (see [Watching execution](#watching-execution)). For a
  pause at one point of a macro there is no sleep action; use
  `{"action": "evaluate", "expression": "new Promise(r => setTimeout(r, 1500))"}`,
  which holds the page still for 1.5s. An `evaluate` is bounded by
  `OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS` (30s by default), so keep each
  pause under that.

## Artifact bundles

Macro artifact tools produce durable, token-light bundles for macro reuse and
verification. The MCP response returns compact status plus paths; full details
stay on disk under `RECORDINGS_DIR/artifacts/macros/<macro>/`.

| Tool | Purpose |
|---|---|
| `macro_artifact_plan` | Dry-run paths and missing args without replaying. |
| `macro_artifact_run` | Replay a macro and write `result.json`, `evidence.json`, and `summary.md`. |
| `macro_artifact_list` | List macro artifact manifests. |
| `macro_digest` | Return a bounded summary of a macro or recording. |
| `macro_export_cli` | Export a saved macro as an import-safe Python CLI script. |

## Watching execution

Every page rendered by a launched browser gets a faint **status pill** injected at
the bottom-center. While a macro runs, the pill shows:

```
[ <id-chip> ]  <elapsed>  ·  <macro-stack> | <action description>
```

- The ID chip color matches the corner badge for the same browser.
- The elapsed counter ticks live (~10Hz) and freezes on completion.
- After a macro finishes the pill stays visible with `<name> | done` (or
  `| failed`) until the next macro starts or `visible: false` is pushed.
- The pill is `pointer-events: none` by default — clicks fall through to the page.

**Alt-click** (Option-click on Mac) the pill to open a themed run-history modal
listing every push for the run with timestamps. Dismiss with the X button, by
clicking the dimmed backdrop, or by pressing Esc.

To slow execution down so you can follow along by eye, pass `slowmo_ms`:

```bash
macro_run instance_id=<id> name=discord-login slowmo_ms=800
```

Or set the default for a session via `OCTOWRIGHT_MACRO_SLOWMO_MS=800`. The pause
happens between the status push and the action dispatch, so the pill always
reflects the upcoming action while you have time to read it. The headed walkthrough
under `examples/pill-status-demo/` shows this end-to-end.

## Running macros in sequence

`macro_run_sequence` replays `names` in order against one live instance, with
`args_list[i]` as the arguments for `names[i]` (a shorter list pads with `{}`).
It returns one shape whatever happens to the steps:

```json
{"sequence": ["login", "orders", "checkout"], "ok": false, "stopped_at": 1,
 "steps": [
   {"macro": "login", "ok": true, "executed": 4, "args_used": {"password": "<redacted>"}, "...": "..."},
   {"macro": "orders", "ok": false, "error": "...", "args_used": {},
    "failure": {"macro": "orders", "failed_at_step": 2, "failed_action": {"...": "..."}, "bundle": {"...": "..."}}}
 ]}
```

- **A failing step is a result, not an error.** With `stop_on_failure=true`
  (the default) the sequence stops after the first failing step: `ok` is
  `false`, `stopped_at` is that step's index, and `steps` holds it and every
  step before it. Later steps are not run and not listed.
- With `stop_on_failure=false` every step runs; `stopped_at` is `null` and
  `ok` is `false` if any step failed. `stopped_at` is always present, and is
  `null` whenever the sequence ran to its end.
- A failed step keeps `ok: false` and `error`, and when its macro raised the
  structured failure a single `macro_run` reports, carries it as `failure`
  (`failed_at_step`, `executed`, `failed_action`, `bundle`, `failed_requests`,
  `page_errors`, ...). It is the same payload, scrubbed the same way, and
  `args_used` is redacted as for any step. For such a step `error` is one
  line -- `macro <name> failed at step <n> (<action>): <first line of the
  cause>` -- rather than the whole payload's text, which `failure` already
  carries. **This also changes `error` under `stop_on_failure=false`**, where
  it used to be that payload text.
- **A missing macro is a failed step**, whose `error` names it, not a refusal
  of the whole call: the steps before it already ran against the browser, and
  the point of the result is to keep them.
- The call still **errors** when it cannot run at all: an unknown instance,
  malformed `names` or `args_list` (refused before any step runs), the
  session's operation gate refusing or breaking, and cancellation.

**Breaking change.** `macro_run_sequence` used to raise the failing step's
error when `stop_on_failure` was on, losing the steps that had passed. A caller
that relied on the call erroring must now check `ok` (or `stopped_at`) in the
result. `octowright test --sequence` is unaffected: it runs its own walk and
reports per-step JUnit cases as before.

## Tools

| Tool | Purpose |
|---|---|
| `macro_save` | Snapshot a recording into a named macro JSON. |
| `macro_list` | List saved macros in bounded pages. `response_mode="families"` rolls the flat namespace up by naming prefix and lists no macros — ask for that first on a large corpus, then narrow with `prefix`/`contains`. |
| `macro_run` | Replay a single macro against a live instance. |
| `macro_run_sequence` | Replay several macros in order on the same instance; a failing step returns `ok: false` + `stopped_at`, it does not error the call (see [Running macros in sequence](#running-macros-in-sequence)). |
| `macro_compile` | Compile YAML macro DSL to canonical JSON; optionally save it. |
| `macro_delete` | Remove a saved macro file. |
| `macro_lint` | Static-analysis pass on a saved macro. |
| `macro_repair_preview` | Non-mutating: list repairable selector actions with semantic replacement candidates. |
| `macro_repair_apply` | Rewrite one brittle selector action into its semantic `click_by`/`fill_by` form and save in place. |
| `macro_artifact_plan` | Validate a saved macro and write/update its artifact manifest without replaying. |
| `macro_artifact_run` | Replay a macro and write a run bundle with `result.json`, `evidence.json`, and `summary.md`. |
| `macro_artifact_list` | List macro artifact manifests, newest first. |
| `macro_digest` | Return a bounded summary of a saved macro or contained recording path. |
| `macro_export_cli` | Export a saved macro as an import-safe Python argparse script. |
| `run_test_suite` | Execute every `[test]`-tagged macro in a directory; emit JUnit XML. |

## Related

- [personas.md](personas.md) — `default_macros` runs after launch (typical use: auto-login).
- [scenarios.md](scenarios.md) — `scenario_run_macro` broadcasts a macro across participants.
- [troubleshooting.md](troubleshooting.md#scenariomacro-failures) — when a macro stops finding selectors.
