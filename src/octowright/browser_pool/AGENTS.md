# browser_pool — launch options, headers, health

Extracted from the root `AGENTS.md` so it loads only when you work in this
directory. The root file remains the canonical index.

### Launch-time extra HTTP headers

`browser_launch(extra_http_headers={...})` sets Playwright's **context-level** `extra_http_headers`, so they ride every request that browser makes — every page, popup, new tab and subresource — for its whole life. Like `base_url`, it is **silent when there is nothing to say**: a launch that passes no headers passes no `extra_http_headers` argument at all, so every pre-existing launch is untouched.

Context level was chosen over a route interceptor on measured grounds, not taste. Across chromium, firefox and webkit (Playwright 1.62, real local server, headers read off the wire): context headers reach the server; a page-level `set_extra_http_headers` overrides them; and — the load-bearing one — the SSRF guard's own `route.fetch()` carries them too. Under a policy that fetch is the *only* fetch of a navigation (the browser is handed what the guard fetched and validated, see the root `AGENTS.md` "Per-hop redirect checking"), so headers it lacked would change what every guarded page is served. A route-level injector has no such guarantee for free, and a *fulfilling* route (`mock_route`) suppresses a context route handler entirely, so a route-based injector would silently skip any mocked pattern.

Values are validated before they can forge a request rather than decorate one: header names must match RFC 7230's token production, values may not contain control characters (a CR/LF ends the header and starts another, so one value could append a second header the caller never wrote), and the map is bounded (`MAX_EXTRA_HTTP_HEADERS`, `MAX_EXTRA_HTTP_HEADER_VALUE_CHARS`) because it rides every request.

`browser_set_extra_http_headers(instance_id, headers)` is the **page-level** companion, also a replayable macro action (`set_extra_http_headers`), for the header a run only learns partway through — log in, then carry the token. Page-over-context precedence is measured on all three engines. It is per **page**, so a popup or new tab opened afterwards does *not* inherit it; that asymmetry is why the launch-time option exists alongside it.

**Record-time redaction is by header NAME.** `press_key`/`evaluate`/`select_option` are scrubbed only under the blanket `all` mode because a selector-less sink genuinely cannot classify its own value — but a header carries its name, and the name says whether the value is a secret. So under the DEFAULT `passwords` policy an `Authorization`/`Cookie`/`X-Api-Key` value -- or any name ending in `-key` (`Ocp-Apim-Subscription-Key`), with `_` read as `-` (`X_API_KEY`) -- is replaced with `<redacted:header>` while `X-Env` stays readable; `all` scrubs every value, `off` scrubs none. Names are never scrubbed — which headers a run set is the diagnostic value, and the name is not the secret. The page always receives the real value. Consequently a macro saved from such a recording holds the placeholder, not the token: replay (and the exported CLI script) **refuse** it with a message naming the fix — parameterize as `"Bearer {{token}}"` — rather than sending it and surfacing a puzzling 401 several actions later.

`browser_inject_headers(instance_id, url_pattern, headers)` / `browser_uninject_headers` are the **per-endpoint** layer (macro actions `inject_headers`/`uninject_headers`), a **`context.route`** handler that `fallback`s with the extra headers merged in. Context, not page: a page route dies at the page boundary, so a caller had to re-register after every page switch and hope they caught them all — and the interesting traffic is often exactly in a popup. Measured on all three engines: a context route sees a popup's requests and the popup receives the header. Reach for it only when headers genuinely must vary by URL: it intercepts, so every matching request pays a handler round trip, where the other two ride requests the browser was making anyway.

**Route order is measured, and its failure is silent.** Two separate rules decide which handler wins, and only one of them is about order.

*Within one level*, handlers run **last-registered-first** — on the page and on the context alike. The context case matters because `ssrf_guard.install_navigation_guard` is itself a context route installed at launch: an injector registered later therefore runs **before** it, so the guard's `route.fetch()` -- now the only fetch of a navigation -- carries the injected headers the browser's own request would have. That is why `launch_helpers.install_context_routes` exists rather than two calls at the call site — it registers the guard first and the scoped launch-header routes second, so they run in the other order. Reversed, the guard's fetch goes out without the injected headers, so the page is served (and the policy checks) a different response from the one the headers would have produced. Pinned by the `tests/test_route_order_live.py` canary, because the guarantee is Playwright's rather than ours and nothing here would otherwise notice it changing.

*Across levels*, order does not enter into it: **page routes are evaluated ahead of context routes**, and a handler that *fulfills* ends the chain. `mock_route` is a page route and `inject_headers` is a context route, so a mock on an overlapping pattern suppresses the injector completely — in **either** registration order — and the injector's handler is not invoked at all (measured on chromium, firefox and webkit; same canary). This changed with the page→context move: while both were page routes, last-registered-first meant only the mock-then-inject order lost, which is the single direction `inject_headers` warned about. Both install sites now log `octowright.session.header_injection_shadowed_by_mock` on an exact-pattern collision; an overlapping-glob collision still cannot be detected and is documented only. Handlers live in `_header_routes`, deliberately separate from `mock_route`'s `_active_routes`, so a mock and an injector may share a pattern without one evicting the other's handler reference. The route callback is a registered gate bypass (`event-critical`), like `mock_route`'s: a route handler must unblock the network request the active operation is awaiting.

**A route glob is a regex, and the match runs where nothing here can bound it.** Playwright compiles a URL glob before matching every intercepted request: `**` becomes `(.*)`, `*` becomes `([^/]*)`, so `**a**a**b` is `^(.*)a(.*)a(.*)b$`. No quantifier is nested inside another, so the blow-up is polynomial rather than exponential — but the exponent is one per wildcard and the *caller* chooses it. Measured against a 129-character URL: 3 wildcards 0.04s, 4 wildcards 0.95s, **5 wildcards 18.0s**. That is an eighteen-character pattern.

The reason this deserves its own note, rather than being one more slow call, is **where it runs**. The match happens inside the **Node driver**, which `BrowserPool` shares across every session (`pool.py`'s single `async_playwright().start()`), so a hostile pattern installed on one browser stalls navigation in all of them — measured at 3000x on a victim browser that had no route of its own. The leader's Python event loop keeps running normally throughout, which is precisely why none of this repo's hang machinery notices: `session/timeouts.bounded` bounds an *awaited Python call* and this is not one, the operation gate sees a healthy session, and `doctor`'s engine probes pass. Playwright's own `timeout=` is enforced in that same wedged driver, so it cannot fire either — a `page.goto(timeout=45000)` was observed still running at 180s.

`url_patterns.validate_url_pattern` therefore refuses the pattern **before** it is forwarded (`MAX_URL_PATTERN_WILDCARDS`, counting wildcard *runs* — `*` and `**` each contribute one group, so counting only `**` would leave `*a*a*a…` unguarded). Refusing in Python is sufficient exactly because the wedge is driver-side: a pattern Octowright never sends is never compiled by anyone. It guards `mock_route`, `inject_headers` and the launch-time `extra_http_headers_urls` — that last one already capped *length* at 2048, which an eighteen-character attack walks straight past. `unmock_route`/`uninject_headers` need no guard; they pop an already-registered handler and compile nothing. The cap is **two** runs, because the cost is the URL length raised to the number of runs and the page picks the URL: it was five, tuned against a 129-character URL, while the same shape at an 8 KB URL took 134 s at three runs (V8; two took 0.06 s). Two runs are still quadratic -- a URL of tens of kilobytes costs seconds per intercepted request -- and closing that needs a linear-time matcher in place of Playwright's regex. The cap is pinned as a **constant** as well as by timing (the timing test checks the polynomial degree at 2 vs 8 KB), because the test that would catch a raised cap is the test that hangs on it.

**Scoping launch headers: `extra_http_headers_urls`.** Context-level headers have no URL filter, so they ride **every** request the browser makes — including cross-origin subresources. On Chromium that makes those requests CORS-preflighted, and a third party that does not echo `Access-Control-Allow-Headers` rejects them outright; measured, and reported from the field as blocked font/CDN requests with a page that never finished rendering. Firefox and WebKit applied the header *below* the CORS check and were unaffected, so this is **Chromium-specific rather than universal** — worth knowing before reproducing it elsewhere. Passing URL globs moves the headers onto scoped **context routes** (`launch_helpers.install_scoped_header_routes`) that still follow popups and new tabs but leave everyone else's requests untouched; the context then carries no unscoped headers at all, or they would apply twice. It exists alongside `browser_inject_headers` because the launch navigation happens *during* launch, which a post-launch call cannot cover. **Scoping is first-hop only**, for these and for `browser_inject_headers` alike: a route's `fallback(headers=...)` override is applied by Playwright to every redirect the request starts, so a matching request answered `302 -> another.origin` carries the headers there (measured on Chromium and Firefox for every header name; WebKit strips only `Authorization`). Under an SSRF policy a *navigation* is the exception -- the guard fetches each hop itself and serves a client redirect, so the next hop is re-matched against the patterns -- but fetch/XHR subresources follow redirects inside the engine whatever the policy. Per-hop scoping is deliberately not built: it would mean fetching every subresource from the route handler, with the CORS, streaming and cookie costs listed under the root `AGENTS.md` "Per-hop redirect checking". That is why a macro credential in an `inject_headers` header needs `forward_on_redirect` even to the session's own origin (`credential_sinks.parse_forward_on_redirect`).

**`timeout_ms` reaches the CSS-selector path, not just the ARIA one.** The trap it guards against is accepting the field everywhere and honouring it only on the semantic path: `macros/runtime._dispatch_click_or_fill` forwards it to `click_by`/`fill_by`, so popping it before the `click`/`fill` fallback — with `session.click` taking no timeout parameter and hardcoding `DEFAULT_ACTION_TIMEOUT_MS` — silently ignores it. So a macro action carrying `timeout_ms` on a selector click linted clean, saved from the dashboard editor, and ran on the 15s default; reported from the field as a failing click costing 15s four times over, with the obvious mitigation turning out to be a no-op. The **MCP tools had the same hole** — `browser_click`/`browser_fill` forwarded it to the semantic pair and dropped it on `session.click(selector)` — so an agent had no working knob either. Both `click`/`fill` now take `timeout_ms` and resolve it exactly as `click_by`/`fill_by` always did.

Two resolution details are load-bearing and pinned by tests. **`None` resolves to the default rather than being forwarded**: Playwright reads an explicit `timeout=None` as *no timeout*, so splatting an action carrying `"timeout_ms": null` would hang forever instead of falling back. **`0` also resolves to the default**, because Playwright reads `timeout=0` as *disable the timeout* — a macro author writing `0` means "don't wait", not "block this run indefinitely". `x or DEFAULT` looks like a null check, so a refactor to `x if x is not None else DEFAULT` would silently reintroduce the hang.

Relatedly, `macros/lint_fields._click_or_fill_allowed` derives its allowed set from the signature rather than naming `timeout_ms` as a literal. A literal there is hand-maintained drift inside the very module whose docstring argues against hand-maintained tables, and a test fails if the literal comes back.

**A browser can say what headers it is sending.** Each `browser_list` entry carries `extra_http_headers`. `extra_http_headers` otherwise reaches `new_context()` and is thereafter known to Playwright alone, which exposes no getter; neither the page-level `browser_set_extra_http_headers` nor `browser_inject_headers` kept a copy either (the latter stores the route *closure*, from which the headers cannot be recovered). A client that tagged traffic with a per-run header and later **adopted** an already-running browser therefore could not tell a current tag from a stale one, and resorted to tracking its own launches in-process — wrong across restarts and blind to other clients' browsers. The three scopes are reported **separately, never merged**, because their reach genuinely differs and a flattened map would assert a precedence that does not hold uniformly: `launch` is context-level and rides every request (unless `launch_url_patterns` narrows it), `page` covers only the active page and overrides the context there, and `injected` are context routes keyed by URL glob. A scope with nothing set is omitted, so `{}` means no extra headers anywhere rather than "not reported". Values are scrubbed by header **name** through `http_headers.redact_headers_for_report`, which shares the recorder's classification but **floors the mode at `passwords`**: `OCTOWRIGHT_REDACT_INPUTS=off` is an opt-in for *recordings* (a 0600 file on the operator's own disk), and honouring it here would turn that into "ship my bearer token to every MCP client". `all` is still honoured, being stricter.

**Request headers are recorded, and returned on request.** Recorded rows carry `headers`. A row holding only url/method/resource_type/status makes every header feature unverifiable from the tool surface — a field report set a launch header, checked here to confirm it applied, saw nothing, and nearly concluded the feature was broken. Scrubbed by header **name** with the same policy the JSONL recorder uses, since a browser sends `Cookie`/`Authorization` on ordinary requests and this output goes to an LLM. Read from the synchronous `request.headers` (`all_headers()` is async and this runs in an event handler), which can omit a few values the async form returns.

`browser_network_requests` returns them only under **`include_headers=True`**, and that is the documented way to verify a launch/inject/page header actually rode a request. The default is off because a header map is most of a row's size — **~900 JSON chars against ~130 without**, measured on a typical Chromium navigation set — and nearly all of it is identical boilerplate (`user-agent`, `sec-ch-ua*`, `accept`) repeated per row, so always-on took an unfiltered read of an ordinary 200-request page from roughly 6.6k tokens to 45k. The same read had **no row cap at all** and could return the whole 5000-entry deque; it now returns `NETWORK_REQUESTS_DEFAULT_LIMIT` (200) rows per call with `returned`/`truncated` in the payload, `limit` up to 1000, and a non-positive `limit` falling back to the default rather than meaning unbounded — an LLM must not be able to remove the cap by passing `0`. When a read is capped, `next_cursor` is the absolute index of the first **matching** row not returned, not the row after the last one returned: the cursor indexes the unfiltered stream, so the other choice silently loses every match the cap left behind. Two in-process readers genuinely need everything and pass `limit=None`: `browser_network_summary` (it aggregates — a capped read would report wrong counts) and `capture_create(kind="network")`, the full-fidelity sink, which also asks for headers since it writes to disk and is read back through `capture_lines`/`capture_search` rather than dumped inline.

**Never restored from a JSONL recording.** `LaunchOptions.from_launch_record` drops it, the same exclusion `channel`/`executable_path`/`launch_args` already carry: a recording is untrusted input (another local user, a poisoned CI step), and a header it could set would attach an attacker-chosen `Authorization`/`Cookie` to every site the relaunched browser visits. It *is* carried by `to_pool_kwargs`, which is the in-memory handoff/relaunch path and is trusted.

### Native Wayland for headed Chromium (`wayland_native`)

Headed Chromium on a Linux Wayland desktop otherwise runs under XWayland, which drops touchpad pinch gestures (field report: zero wheel/touch/pointer events reached a page while pinching). `browser_pool/wayland.py` resolves a per-launch `wayland_native` (argument > `OCTOWRIGHT_WAYLAND_NATIVE` > auto) and, when on, `_chromium_args` adds the fixed `WAYLAND_NATIVE_ARGS`. Like the `OCTOWRIGHT_DISABLE_GPU` escape hatch in `options.py`, it is a **boolean over a fixed flag set**, not more `launch_args`: arbitrary argv stays behind `OCTOWRIGHT_ALLOW_EXECUTABLE_PATH`, and a boolean grants no new power. Auto requires Linux + Chromium + headed + a `WAYLAND_DISPLAY` socket that exists; headless is *reported* rather than refused even for an explicit `True`, because headed-ness resolves late and a handoff can flip it.

Three things are load-bearing. **`--enable-features` is merged** (`merge_enable_features`, only when Wayland is on): Chromium reads only the last such switch and Playwright passes `--enable-features=CDPScreenshotNewSurface` before ours, so a bare switch would silently turn Playwright's feature off. **Only auto falls back** to X11 (`launch_execution.open_with_wayland_fallback`), once, inside one `pool.launch`, and **only when the browser blamed Wayland** (`wayland.mentions_wayland`) -- any other failure (locked profile, missing library) would fail identically on X11, and retrying it doubled the launch time and reported the unrelated error as the Wayland reason -- engine health and launch metrics see one outcome. **The retry forces X11** (`x11_retry_kwargs`, rewriting in place so no second tile slot): it replaces the `--ozone-platform=wayland` this module added with the constant `--ozone-platform=x11`. Merely stripping it is not enough -- with no ozone switch Chromium picks the platform from `XDG_SESSION_TYPE`, which on a Wayland desktop is Wayland again, and the retry died like the first attempt. Measured on chromium-1243 under `xvfb-run` with `XDG_SESSION_TYPE=wayland` and a dead `WAYLAND_DISPLAY`: no switch fails, `--ozone-platform=x11` launches, the last `--ozone-platform` wins, and no environment variable (`OZONE_PLATFORM`) overrides it. Only the switch this module added is replaced; a caller's own `--ozone-platform` in `launch_args` comes later, is left as given, and still wins. **The failure text matches the dead-driver markers.** Measured on chromium-1243 with a missing socket: `TargetClosedError: ... Target page, context or browser has been closed` with `Failed to connect to Wayland display` in the browser log. That text used to read as a dead *shared* driver, which `_reset_driver` stops -- taking every live browser with it; the pool now confirms with a liveness probe first (see **A launch failure that only reads like driver death**), and `wayland.WaylandLaunchError` is excluded from `is_driver_dead_error` by type, so it never reaches that probe -- which falls back to "dead" when it cannot read Playwright's internals. Auto catches the failure to fall back within the same launch; an explicit request raises `wayland.WaylandLaunchError`, whose message leads with the browser's own Wayland complaint (the original stays on `__cause__`). "Did the browser blame Wayland" must skip Playwright's argv echo (`<launching> ... --ozone-platform=wayland`), which every Wayland launch failure carries -- the live fallback test caught the reason reporting that line. `tests/test_wayland_native_live.py` (opt-in `OCTOWRIGHT_LIVE_WAYLAND=1`; run the fallback case under `xvfb-run`; it sets `XDG_SESSION_TYPE=wayland` itself) pins both paths against real Chromium. The persisted value is the *request*, so auto stays auto across relaunch and recordings.

### Host-relative navigation

A macro is the behaviour; the persona is the *where*. The browser context resolves relative paths against a `base_url`, so one macro replays against any deployment by launching it as a different persona.

`base_url` resolution, most specific first: an explicit `LaunchOptions.base_url` (for a library caller with no persona to speak for it — a suite pinned to a dev stack), else the launch profile's persona `default_url`. Both cases are **deliberately silent when there is nothing to say**: a profile name need not be a saved persona, and a persona need not declare a `default_url`. Neither passes `base_url=None` — they pass nothing at all, so absolute URLs and every pre-existing macro keep working untouched.

`browser_navigate` accepts a **single** leading slash (`/orders`): same-origin by construction — no scheme to deny, no new host to reach — so `_reject_unsafe_url` lets it through to Playwright for resolution. **Two** slashes is protocol-relative (`//evil.test/x` is a different host) and still goes through the full absolute-URL checks. That relaxation is only sound if the inherited origin is itself trusted, so a `base_url` is validated through the same guard every navigation uses — otherwise it would be a way to reach a host the SSRF policy refuses by writing `/` in a macro.

### Protected close behavior

`protected=True` marks a browser as user-owned. Close-capable tools must refuse protected browsers unless the caller explicitly passes `force=True`. This applies to `browser_close`, `browser_close_all`, and `browser_capture_and_close`; the capture-and-close tool checks protection before taking screenshots or snapshots so a refused call has no capture side effects. Internal rollback/teardown paths that are recovering from errors use `force=True` intentionally.

Headed (user-facing) browsers are `protected` **by default** so an agent's
reflex `browser_close` can't destroy a window the user is watching: when a
launch doesn't pass `protected` explicitly, a resolved-headed, non-ephemeral
browser gets `protected=True` (reason `headed_default`), while headless
(CI/agent-internal) browsers stay closeable. Precedence: explicit `protected`
arg > `OCTOWRIGHT_PROTECT_BROWSERS=1` (all) > `OCTOWRIGHT_PROTECT_HEADED`
(headed, default on) > unprotected. The refusal message is tailored by
`session.protected_reason`. Ephemeral headed browsers stay closeable
(throwaway intent). Internal relaunch/handoff/teardown close with `force=True`
and are unaffected.

### What a handoff or relaunch carries

Handoff, fluid relaunch and the driver-death / process-crash reopen each launch a replacement for an existing browser, and each used to rebuild its options by hand from session attributes. Every copy list had drifted from `LaunchOptions`: launch headers and their URL scoping, an explicit `base_url`, `disable_gpu`, the HAR mode/filter/content, the viewport and the badge were lost on all three, and the driver path also dropped `protected`, `disable_automation_controlled`, `wayland_native` and `headed`. An anonymous `session=True` browser lost its whole state -- its tmpdir is keyed by instance_id, and the replacement got a new one (measured on Chromium: different directory, `localStorage` empty).

The session now keeps the options it was launched with (`BrowserSession.launch_options`, written by `replacement.recorded_launch_options` with the resolved `headed`, the promoted profile and a session launch's directory key folded in), and `replacement.ReplacementSource` derives every replacement from them. A field is carried unless it is named, with its reason, in `NOT_CARRIED` (`executable_path`, `launch_args`: chosen per launch, never implied -- the `browser_launch` description already promises this) or `SET_BY_REPLACEMENT` (`url`, `trusted_launch_url`, `protected` from the session's current state, a rotated `har_path`). A new `LaunchOptions` field is therefore carried by construction; `tests/test_relaunch_carries_launch_options.py` fails on one that is neither carried nor named. **`channel` is carried**, from the live session's options only (`from_launch_record` still drops it -- a recording is untrusted). It used to be in `NOT_CARRIED` because a carried channel cannot notice that the named build was uninstalled since; that case is now handled where it happens: `replacement.launch_replacement` retries a replacement once without the channel when Playwright refuses that exact channel as not installed (`Chromium distribution '<channel>' is not found|supported`), logs `octowright.browser.replacement.channel_dropped`, and the caller says so -- `warnings` on the handoff/fluid-relaunch result, `channel_dropped` on a driver-death relaunch's lost record. Any other failure, or one naming a different distribution, is not retried (`tests/test_relaunch_carries_channel.py`). Deliberate departures stay explicit at the call site: handoff's `headed` (`None` now keeps the original's rather than re-defaulting), and fluid relaunch's `FLUID_OVERRIDES` (headed, and no pinned viewport -- fluid exists so the viewport follows the window).

`session_key` is the internal option that carries a session directory across: `session_name` prefers it over the label, it grants nothing a label does not, and like `trusted_launch_url` it is never read from a JSONL recording and refused at every external entry point (`LaunchOptions.from_external_mapping`). The driver path reads the replacement source at capture and keeps it out of the `pool.lost_sessions` record, which status serialises and which must not carry launch headers.

### A launch failure that only reads like driver death

`driver_health.is_driver_dead_error` matches error **text**, and one of its markers -- "Target page, context or browser has been closed" -- is also exactly what Playwright raises when **one browser process exits during launch**. Measured on Playwright 1.63: an `executable_path` that exits at once, or Chromium with `--ozone-platform=wayland` and a dead socket, raises `TargetClosedError: BrowserType.launch: Target page, context or browser has been closed` while the driver is fine and every other browser stays usable. `_launch_with_driver_retry` used to reset on that text alone, and `_reset_driver` stops the shared driver and `driver_relaunch.on_driver_reset` evicts every live session -- one bad `launch_args`, locked profile, sandbox crash or missing library took every browser down.

The text is now only a suspicion. Before resetting, the pool asks **`driver_health.driver_confirmed_dead(...)`**, which says yes only on evidence:

- **Authoritative local flags first, no round trip.** After `Playwright.stop()` the connection's `_closed_error` is set. After the node driver is SIGKILLed, `_closed_error` stays `None` (measured) but the transport's `on_error_future` is done and the process has a `returncode`. Any of these is dead.
- **Then a real protocol round trip**: `localUtils.traceDiscarded` with an empty `stacksId`. The driver returns before any lookup for a falsy id, so it cannot touch a real tracing session. Measured: 0.57ms median / 4.6ms max idle, 1.1ms median / 7.4ms max with three pages navigating; after SIGKILL it fails in under 1ms with `Connection closed while reading from the driver`. An answer -- success, or a protocol error the driver sent back -- is alive; a transport error (which Playwright only ever delivers after flagging the transport) or `TargetClosedError` is dead. No answer within `DRIVER_PROBE_TIMEOUT_SECONDS` is **not** death -- see **A hung driver** below.
- **Bounded with `asyncio.wait`, never `asyncio.wait_for`.** Cancelling a Playwright channel send runs `Connection._abort`, which waits for the driver to acknowledge: measured, `wait_for(send, 0.5)` against a SIGSTOPped driver did not return until the driver was resumed. The unanswered send is left to finish or fail on its own.
- **It neither misreads nor consumes a stored listener error.** `Channel._inner_send` raises -- and clears -- `Connection._error` (an exception a sync event listener raised, which Playwright saves "to throw at the next API call") before it sends anything. Measured on 1.63: a `console` listener raising `ValueError` made the probe call a healthy driver dead and swallowed the error the caller's next call was meant to get. `_start_probe` lifts the stored error off for the send and puts it back once the send has started; a listener error that lands in that window is seen by `_probe_send` before the send's own check (no await separates them), left where it is, and the round trip tried once more. So any other exception from the send is the probe's own failure: it reads as "cannot tell" (dead, as with unreadable internals) and is never stored on `Connection._error`, where it would have surfaced as the caller's next, unrelated call failing. The probe task is always retrieved (a caller cancelled mid-wait leaves it running), and a probe task that ends cancelled is a non-answer, not an exception. `TargetClosedError` is matched by class name and the base `Error` comes from `playwright.async_api`, so no private Playwright module is imported at daemon start.
- **No handle, or internals it cannot read, is dead** -- the old reset-on-text behaviour, not "never reset". `tests/test_launch_death_keeps_driver_live.py` runs the probe against a real driver, so a Playwright upgrade that moves these private attributes fails a test instead of silently degrading.

**A hung driver: a timeout does not reset, and a reset's stop is bounded.** Two findings pulled in opposite directions -- a probe timeout counted as dead (evicting every browser for a driver that was merely busy), while a reset that did fire on a hung driver awaited `Playwright.stop()` unbounded. Measured on 1.63 to choose between them: every real death leaves a local flag (SIGKILL: `on_error_future` done, `returncode` set, probe answers in <1ms; `stop()`: `_closed_error`); a busy driver is silent for a while (the round trip stalled up to **243ms** while the driver zipped a trace and flushed a 120MB embedded HAR -- the stall scales with the artefact); a SIGSTOPped driver shows no flag and never answers, and `stop()` against it is still pending after 3s but returns 10ms after the process is killed. So **timeout => not confirmed dead => the original error is re-raised, no reset**: the silent case is overwhelmingly a busy driver, and a launch against a truly hung driver hangs rather than raising a dead-driver error, so this path is not how a hung driver is reached anyway. And the reset itself, still reachable when the internals cannot be read, goes through `driver_health.stop_driver`: `stop()` bounded by `DRIVER_STOP_TIMEOUT_SECONDS` (5s) with `asyncio.wait`, then the driver process killed, which lets the stop finish. `tests/test_launch_death_keeps_driver_live.py` pins both halves against a real SIGSTOPped driver (POSIX).

**The verdict is about the driver the failed launch used.** `_launch_with_driver_retry` notes the driver generation (`_driver_restarts`, moved under `_pw_lock` exactly when the handle is replaced) before the attempt. Several launches failing together on one dead driver used to each probe and reset whatever `_pw` held *by then*: a later one probed the replacement and re-raised (a missed retry), or found `_pw` empty and reset again -- stopping the new driver and evicting every browser a second time. Now a launch whose generation has moved retries once on the replacement without probing, and `_reset_driver(expected=...)` does nothing unless `_pw` is still the handle that was confirmed dead.

Alive: no reset, no retry; the original launch error is re-raised unchanged (engine health records it as before) and `octowright.pool.driver_death_suspected_but_alive` is logged at info. Dead: the existing reset + one retry. `_launch_with_driver_retry` is the only reset site; relaunch, handoff, roster/scenarios and driver-relaunch all launch through `pool.launch`, so they get the confirmation too.

### Per-engine launch health

`BrowserPool` tracks the last launch outcome for each engine kind (`chromium`/`firefox`/`webkit`) and surfaces it at `octowright_status()["pool"]["engine_health"]`, e.g. `{"chromium": {"outcome": "ok", "at": "2026-08-29T12:00:00.000Z"}, "webkit": {"outcome": "error", "at": "...", "error": "TimeoutError"}}`. A fourth key, `unknown`, keeps both this block and the `kind` metric label bounded to four values instead of growing one permanent entry — and one permanent metrics time series — per distinct string a caller passes, since `kind` reaches `BrowserPool.launch` straight from the caller. It is not a fourth engine. Two clamps apply it and only one is still live: `launch`'s runs before `LaunchOptions.validate` does, so it bounds the `octowright.browser.launch` span attribute and the `kind` metric label for a request that is about to be refused (pinned end-to-end by `tests/test_engine_health.py::test_an_unsupported_kind_is_clamped_before_it_reaches_any_label`, which intercepts the span rather than the caller). The re-clamp inside `_record_engine_health` is now unreachable through `launch` — an unsupported `kind` is rejected as an `InvalidRequestError` (below) and recorded nowhere — and is retained only so a future direct caller of that method cannot put an arbitrary string into a never-evicted dict echoed into every `octowright_status()`. This exists because a real incident's diagnosis spent about an hour of a 12.6-hour wedge establishing one fact — "WebKit is broken on this machine, Chromium is fine" — even though the pool already saw every launch and every failure per engine; it just never said so. Each kind is tracked independently (`BrowserPool._record_engine_health`, called from `BrowserPool.launch` after `_launch_with_driver_retry` resolves), so one engine failing does not touch another's last-known state. A kind never launched is **absent** from the block rather than reported healthy — "no data" and "fine" are different answers, and conflating them is what made the original diagnosis slow. On failure, `error` carries the exception's **class name only, never its message** — a launch failure message can carry a filesystem path or a profile name, while the class name is the diagnostic signal and carries nothing sensitive (the same reasoning `octowright_browser_launch_failed_total` uses for its `error` label).

**A caller's mistake is not an engine fault.** Everything `launch` wraps answers
one question — "is this engine working on this machine" — and a request that
never reached an engine cannot answer it. Recorded anyway,
`browser_launch(url="file:///etc/passwd")` left this block reporting `chromium:
{"outcome": "error", "error": "ValueError"}`; since only the class name is kept,
that is byte-identical to a genuinely broken engine. It was read as one, retried
on firefox for the identical signal, and cost about an hour — inverting the
block's entire purpose (issue #214).

The input guards therefore raise **`octowright.request_errors
.InvalidRequestError`**, and `launch` re-raises it without recording, as does
`_metrics.launch_span` for `octowright_browser_launch_failed_total{kind, error}`
— the same lie in metrics form, told to whoever alerts on per-engine launch
failures. The exception is still recorded on the *span*: a trace is a record of
what happened to one request, where a refusal belongs, rather than a per-engine
health signal a refusal can only corrupt.

**Classified by type, not by position, because position does not work here.**
The obvious repair is to hoist the checks above the recording window; it was
tried first and closes exactly the guards that happen to be hoistable — the
target URL and `LaunchOptions.validate`. Two are structurally *inside* the
launch pipeline and cannot be lifted out of it: `har_path` containment needs the
session's log path and the pool's recordings root, and `base_url` validation
needs the persona lifecycle lock held. Both are MCP-surface fields an LLM sets,
and `base_url` needs no caller mistake at all — a saved persona's `default_url`
lands there, so under `OCTOWRIGHT_SSRF_POLICY=block-private` one persona
pointing at an internal host would report its engine broken on every launch. A
guard raising the type is classified correctly wherever it runs.
`InvalidRequestError` subclasses `ValueError`, so every existing `except
ValueError` still catches it and the conversion needed no call-site audit.

**An option Octowright cannot read is refused, not dropped.**
`LaunchOptions.from_mapping` reads every key by name, so anything it did not
recognise was silently discarded while the caller went on believing the option
applied. The one that bit is `headless` — Playwright's OWN parameter name, and
therefore the natural guess — so `pool.launch(kind="chromium", headless=True)`
launched a **headed** browser. It was found the expensive way: a crash probe
written to drive `chrome-headless-shell` drove headed Chrome instead, and the
whole run had to be discarded and repeated. Unknown keys now raise
`InvalidRequestError` naming every one of them, with `headless` additionally
pointing at `headed` because the sense is inverted — its own trap. Refusing is
what this repo already does with a flag the caller believes took effect (`serve
--wait-ready` rejects `--no-singleton` rather than ignoring it), and the type is
the one above precisely so a caller's mistake is not filed as an engine fault.

The accepted set (`CALLER_SETTABLE_FIELDS`) is **derived** from the dataclass
fields rather than listed, minus `protected_reason`, which `resolve_protected`
writes as an *output* — a caller supplying it would be describing a decision not
yet made. It is a module constant rather than a method because `from_mapping`
runs on every launch and the value never changes.

**The rejection is also what lets the construction be derived.** With nothing
unknown left, `from_mapping` is `cls(**options)`. It previously hand-wrote one
`options.get("...")` per field — 28 of them, 11 restating a default the
dataclass already declares — so a new field had to be added in two places, and
a test scraped this function's own source to prove the two agreed. Deleting
that list makes "accepted" and "read" the **same fact** rather than two facts
kept in sync, and the drift guard has nothing left to guard. Behaviour-
preserving: every one of those 11 explicit defaults was verified identical to
the dataclass's before the list was removed.

**`to_pool_kwargs` derives from the SAME set, and that is the point.** It
hand-listed 27 keys and omitted `base_url` — a caller-settable field
`from_mapping` reads and `launch_execution` consumes — so
`LaunchOptions(base_url=...).to_pool_kwargs()` dropped it silently. That is the
`headless` defect in the other direction, and a guard that only inspects
*incoming* keys structurally cannot catch it: the loss happens on the way out.
Both directions now derive from `CALLER_SETTABLE_FIELDS`, so a new field is
transported and accepted without editing either list, and the round trip is
pinned by **equality** (`from_mapping(o.to_pool_kwargs()) == o`) rather than by
the weaker "the round trip is accepted", which is precisely what let `base_url`
hide. `protected_reason` is the single exclusion on both sides, being an output
of `resolve_protected`.

**Every boolean option must be a boolean.** `validate()` checks each field annotated `bool` (and `bool | None`, where null means auto/unset) by type, the set derived from the annotations. Truthiness was the trap: only `headed` was strict, so a poisoned launch record's `ephemeral: "false"` relaunched a browser ephemeral, and the same string reached `from_mapping` from an HTTP body or a roster spec. `from_launch_record` checks `har` itself, because it is folded into a derived value before `validate()` sees it.

`_MISLEADING_ALIASES` (one entry, `headless`) is a deliberate special case and
not the hand-kept-table shape this file warns about elsewhere: a missing entry
costs a plainer message and a stale one costs a needless hint, so it cannot
drift into being *wrong*. It also carries what no derivation can produce —
`difflib` maps `headless`→`headed` but cannot know the sense is inverted, which
is the whole value of the message. If it is ever extended, ordinary near-miss
typos (`viewport_width`) belong in a generic `difflib` pass; reserve the map for
inverted or renamed semantics.

Strictness was checked against every caller before adoption rather than after:
the internal paths (`relaunch`, `driver_relaunch`, `scenarios`, the
recording-replay route) all build explicit dicts, and the `to_pool_kwargs` →
`from_mapping` round trip is clean. `spawn_roster` used to build one too, from a hand-kept key list that
silently dropped the documented `protected` and ignored unknown keys such as
`headless`; it now goes through `roster.roster_launch_kwargs` --
`from_external_mapping(...).to_pool_kwargs()` with explicit nulls dropped, so a YAML
`key:` means unset for every key. `from_external_mapping` is `from_mapping` minus
`INTERNAL_ONLY_LAUNCH_FIELDS` (`trusted_launch_url`, `session_key`, which only
handoff/relaunch set and `browser_launch` does not expose); `POST /api/sessions`
goes through it too, after it was found accepting both, and
`tests/test_internal_launch_options_closed.py` fails on any module outside the
pool that builds options from a mapping without it. An unknown key fails that spec into
`errors`. Only `POST /api/sessions` can otherwise carry
arbitrary keys, and its validation runs **inside** the route's `try`, so an
unrecognised field becomes a **400 naming it** instead of a launch that ignored
half the body. That placement is load-bearing rather than incidental: called
above the `try` the `InvalidRequestError` escaped every handler — the app is
built with no `exception_handlers` and `guard_sensitive_http` re-raises — so a
client typo answered **500** with the key nowhere in the body, and paged
whoever watches the 5xx rate for a caller's mistake. Three documents asserted
the 400 while the code did the 500, because nothing exercised the route;
`tests/test_http_server_writes.py` now does.

`browser_launch` has a typed signature and was never affected — this is a
library- and HTTP-caller footgun only. A refusal raised inside
`BrowserPool.launch` lands in `octowright_status()["pool"]["refusals"]` as
`by_guard` `{"browser_pool.options": N}` with no wiring, since that tracker
reads the module from the traceback. **The HTTP route's refusal does not**: it
validates before entering `pool.launch`, so nothing reaches the tracker. That
is the honest scope — the 400 is the signal on that surface, not the counter.

**Honest scope: nothing about the classification is inherited.** Both sinks
test `isinstance(exc, InvalidRequestError)`, so a new check written with the
formerly conventional `raise ValueError(...)` would be filed as machinery
failure and silently recreate this bug — and a hand-maintained list of guards
in a test is documentation, not enforcement.
`tests/test_launch_guard_classification.py` AST-scans the nine modules whose
`ValueError`-shaped raises are launch-reachable input checks (`_paths`, `ssrf`,
`url_patterns`, `http_headers`, `persona_trust`, and `browser_pool/`'s `options`,
`launch_helpers`, `launch_execution`, `launch_pipeline`), plus the two URL
guards in `session/core_page_mixin.py` by function (`_reject_unsafe_url`,
`_check_url_shape` -- that mixin also raises ordinary `ValueError`s nowhere
near a launch), and fails on a bare `ValueError`. That is the whole of the enforcement: a guard added in some
*other* module is a maintenance requirement the scan cannot see.

**Refusals get their own aggregate, because removing the false signal removed
the only signal.** `octowright_status()["pool"]["refusals"]` reports `{total,
by_guard, last_at}`. Without it, a client regression spamming invalid requests
shows a perfectly healthy daemon on an ordinary deployment: `engine_health` is
now (correctly) silent, and `octowright_launch_refused_total` is a noop unless
`PROVIDE_METRICS_ENABLED` is set, which is off by default. A climbing `total`
beside a healthy `engine_health` says *a client is sending bad requests*, which
is a different remedy from *this machine is broken*.

Deliberately **not** an `incidents` category. That ring is 25 entries shared
across every category, and its own docstring notes a repeatedly-firing category
evicts the others — a refusal flood is the highest-frequency event the daemon
can see, so recording each one would push out the renderer-crash and driver
records the ring exists for. Aggregates need no eviction policy.

`by_guard` names the **module that raised**, read from the traceback rather
than tagged by each guard, so a guard added later is attributed without
touching it — the enforcement problem the `InvalidRequestError` classification
already hit once. The key is code, never caller data, and is capped
(`refusals.GUARD_KEY_CAP`) as the same belt-and-braces the `kind` clamp
applies, since this dict is never evicted and is echoed into every
`octowright_status()`. The offending url/path is **never kept**: it is the
caller-supplied string `engine_health` and `launch_span` both already refuse to
retain or export, and keeping it here would undo both. Unlike `engine_health`,
the block is always present — `total: 0` is a complete answer, where "this kind
was never launched" is not.

Relatedly, `reject_unsafe_path` appends the offending path to its message, so a
`label=` that interpolates the same path printed it twice — the live rejection
read `screenshot path '/tmp/x.png' '/tmp/x.png' resolves outside '…/sessions'`,
from four of twenty call sites. The dedupe is in the helper rather than in four
labels because `label` is forwarded verbatim through wrappers
(`artifacts.paths.ArtifactStore._contained`), so where a label is built and
where it is rendered are different modules and a call-site scan cannot see the
forwarded case. `label` names the ARGUMENT (`"har_path"`); a label naming a
*distinct* input (`macro name 'x'`, where the name is not the resolved path) is
useful and unaffected.

### The Chromium 153 first-download crash, and telling a crash from a close

**The crash.** Chrome for Testing 153.0.8010.12 (`chromium-1243`, Playwright
1.63 -- what a fresh install of the published wheel resolves to, while this
which this repo's lock now pins too) kills its own **browser process**
~0.3 s into the first download of a headed run whenever the profile's
`History` holds a download row at startup and Playwright controls downloads.
It is a use-after-free on the UI thread (`Received signal 11 SI_KERNEL ...
General Protection Fault`, registers full of PartitionAlloc's `0xcd` freed
byte), not a CHECK -- there is no message to quote; the field's earlier SIGTRAP
attribution came from unrelated `chrome-headless-shell` renderers in a
concurrent test run, and `/var/log/apport.log` shows the headed browser pids
dying of signal 11 (one, on 09-23, of SIGTRAP -- its message was not captured). Nothing in Octowright causes it:
raw Playwright with no Octowright code, no extension and no download listener
crashed 7/7. `download_history.prune_download_history` deletes the rows before
every Chromium persistent launch -- a `profile` AND a `session=True` tmpdir,
which is reused per label and so carries rows into its next launch (full
matrix in its docstring; run via `asyncio.to_thread`, awaited before launch;
`OCTOWRIGHT_PRUNE_DOWNLOAD_HISTORY` in `docs/env-vars.md`). It is per launch
because Chromium re-inserts the rows from `shared_proto_db` at startup.
`tests/test_download_history_live.py` is the live proof (profile and session
tmpdir) -- meaningful on `chromium-1243`, which the lock now pins; `chromium-1234`
does not crash.

**Detection.** `process_crash` resolves each persistent context's browser pid
from `/proc` just after session construction
(`launch_publish._prepare_session_before_publication`, through
`process_crash.resolve_browser_process`, i.e. in a worker thread; `stat` is read
only for a process whose argv already named the profile) and judges the exit at
the first evicting close signal (`listeners._accept_external_close`). Four
things are load-bearing:

- **Judge only the live identity.** The close events Playwright fires for
  Octowright's OWN close arrive after the session left `_sessions`, when the
  process is already gone -- judging them would report every agent close as a
  crash. `_accept_external_close` skips the verdict for a non-current identity.
- **Judge at the first evicting signal, not later.** Liveness is only
  meaningful then: on a Firefox user close the context `close` (and the
  `save_as` rejection) arrive after a clean exit. For the same reason the
  download path waits for the verdict (`wait_for_exit_verdict`) instead of
  sampling.
- **Only lock evidence may reopen.** An unresolved pid, unreadable `/proc`, or
  reused pid all read as a close. The other misread -- an orderly close seen
  after a loop stall, on an engine judged by liveness alone -- reads as a
  crash, so the incident records `evidence` (`singleton_lock` / `liveness`) and
  a `liveness` crash is labelled but never reopened under
  `OCTOWRIGHT_DRIVER_RELAUNCH`. A liveness "crash" whose every page the
  client itself asked Playwright to close is a close instead
  (`_client_closed_every_page`): headless WebKit exits 8-11 ms after its last
  page closes, which a loaded runner's loop overshoots. Neither is a session whose teardown a close
  already owns (`pool._closing_sessions`, or the gate's `close_reserved`, read
  BEFORE the acceptance seam installs its own reservation): a draining
  `browser_close`, a handoff, a fluid relaunch.
- **Eviction never depends on classification.** `_accept_external_close`
  catches a classifier failure, logs `octowright.browser.exit_classification_failed`,
  and still evicts as an external close; `classify_external_close` sets the
  verdict event in a `finally`, so a waiting download save is not left to time out.

Chromium rewrites `/proc/<pid>/cmdline` into ONE space-joined string, so the
matcher has a second path for that shape; WebKit's root is the `pw_run.sh`
wrapper (killing only the wrapper leaves MiniBrowser and its pipe alive, and
nothing fires -- crash tests must signal MiniBrowser).

### Type-checking the injected assets (`_assets/*.js`)

The five init scripts injected into every page are **type-checked in place**,
not compiled: `npm run typecheck:assets` (`make typecheck-assets`) runs `tsc
--checkJs` over them via `ci/js-typecheck/tsconfig.json`, wired into CI's
frontend job.

They stay plain `.js` deliberately. They are read from the wheel at runtime,
templated, and injected as strings, so compiling them would add a build
artifact that can drift from its source — for code that runs inside every page
Octowright drives, in a repo that already needs `check_wheel_assets.py` because
packaging can silently drop assets. `checkJs` buys the type safety with no
build step and nothing new to ship.

**The settings are chosen from measurement, not taste.** Under full `strict`
the five files report 72 errors, 62 of them `TS70xx` "implicitly has an 'any'
type" — which measures annotation coverage, not correctness, and buries the
findings that matter. With `noImplicitAny` off the same run reports 10, all of
them real classes (`strictNullChecks`, unknown properties, `this` context).
`strictNullChecks` is the one that earns its place: it caught a live defect in
`title_tag.js`, where `desc.get`/`desc.set` were read from three places and
guarded in only one, so on an engine without that descriptor the script would
throw and swallow twice per `<head>` mutation forever. No test, lint rule or
mutant covered it — and a `biome` pass over all five files reports only style
nits, so linting alone would not have found it either.

Two things are load-bearing about the setup:

- **The `__PLACEHOLDER__` tokens are declared in `injected-globals.d.ts`**, typed
  to the JSON shape of the `json.dumps(...)` value at the substitution site in
  `visuals.py`. Without them `tsc` reports 13 "Cannot find name" errors that are
  not defects — which is most of why these files went untyped for so long. A
  placeholder typed `any` there buys a green check and no safety, so keep them
  accurate.
- **`scripts/check_js_typecheck_coverage.py` asserts the include glob still
  matches every asset.** A glob that stops matching does not fail: `tsc` reports
  success having checked nothing, so the gate goes green covering zero files.
  Identical output either way, which is why it gets a guard rather than trust.

The `window` bindings and the binding *results* are deliberately loose
(`Record<string, any>`). They cross into `dict[str, Any]` on the Python side —
`_viewport_action` returns three different shapes by action — so a precise
hand-written interface would be fiction that drifts from `pool.py` with nothing
keeping the two in sync.
