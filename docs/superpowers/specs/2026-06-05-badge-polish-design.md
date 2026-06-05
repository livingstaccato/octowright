# Badge Polish + Docs Fixes — Design Spec
Date: 2026-06-05

## Scope

Six changes across the badge JS, visuals layer, defaults, browser pool, skill docs, and README:

1. Badge opacity — configurable, default reduced to 0.35
2. Badge positions — expand from 4 corners to all 8 slots
3. Badge Alt-click info popup — same interaction model as the macro pill
4. `nav_warning` docstring — document the survive-navigation-failure behavior
5. README + `.octowright` scaffold — port reference and caching note
6. Reliable new-tab redirect — Cmd+T always opens `/new-tab`, not Chrome's default

---

## 1. Badge Opacity

**Default:** `0.35` (down from `0.72`).

**Configuration:** `OCTOWRIGHT_BADGE_OPACITY` env var (float `0.0`–`1.0`). Defaults to `0.35` when unset. Stored in `defaults.py` alongside other badge defaults. Added to the env var table in `AGENTS.md`/`CLAUDE.md`.

**Implementation path:**
- `defaults.py`: add `BADGE_OPACITY = float(os.environ.get("OCTOWRIGHT_BADGE_OPACITY", "0.35"))`
- `visuals.py`: pass `opacity` through the template variable injection as `__OPACITY__`
- `badge.js`: replace the hardcoded `opacity: "0.72"` with `opacity: String(__OPACITY__)`

No change to the popup or Alt-click overlay (those use their own opacity values).

---

## 2. Badge Positions — All 8 Slots

**New values** added to `_BADGE_POSITIONS` in `visuals.py`:

| Key | Vertical anchor | Horizontal anchor | Notes |
|---|---|---|---|
| `top-center` | `top: 8px` | `left: 50%; transform: translateX(-50%)` | new |
| `bottom-center` | `bottom: 8px` | `left: 50%; transform: translateX(-50%)` | new |
| `left-center` | `top: 50%; transform: translateY(-50%)` | `left: 8px` | new |
| `right-center` | `top: 50%; transform: translateY(-50%)` | `right: 8px` | new |

Existing 4 corners unchanged.

**badge.js change:** The current position system uses `styles[POS.vertical] = "8px"; styles[POS.horizontal] = "8px"`. Center positions require an additional `transform` value. The `POS` object gains an optional `transform` field; `badge.js` applies it when present: `if (POS.transform) styles.transform = POS.transform`.

**`_BADGE_POSITION_DEFAULT`** stays `"bottom-right"`.

**Validation and docstrings** updated in `options.py` and `lifecycle.py` to list all 8 values.

---

## 3. Badge Alt-Click Info Popup

**Interaction model** (identical pattern to `macro_pill.js`):

- Normal state: `pointer-events: none` — badge never intercepts page clicks.
- Alt held: `keydown` listener detects `altKey`; badge switches to `pointer-events: auto`, opacity raised to `0.9`, cursor `pointer`.
- Alt released: returns to normal state.
- Alt+click: opens info overlay; badge returns to normal pointer-events.
- Dismiss: Escape key, or click outside the card.

**Popup card** (dark overlay anchored near the badge, same position as the badge itself):

| Field | Value |
|---|---|
| `id` | instance ID hex string |
| `label` | label or `—` if null |
| `profile` | profile or `—` if null |
| `kind` | `chromium` / `firefox` / `webkit` |
| `url` | current page URL |
| `protected` | `yes` (green) / `no` |

**Links section** below the info rows:
- `dashboard ↗` — opens `__DASHBOARD_URL__` in a new tab
- `session recording ↗` — opens `__DASHBOARD_URL__#session/__INSTANCE_ID__` in a new tab

**Footer:** `Esc or click outside · Alt+click to reopen`

**Template variables** baked in at inject time by `visuals.py` / `launch_helpers.py`:
- `__DASHBOARD_URL__` — resolved from `get_default_url()` base (strip `/new-tab` suffix, use origin only)
- `__INSTANCE_ID__` — the session's `instance_id`
- `__OPACITY__` — badge opacity (used by normal badge state; popup uses its own values)
- `__TAG__`, `__COLOR__`, `__POS__` — existing variables, unchanged

**Styling:** Matches the macro pill modal aesthetic — dark card (`#1a1a2e`), monospace font, subtle borders.

---

## 4. `nav_warning` Docstring

`browser_launch` and `browser_quick_launch` docstrings in `lifecycle.py` gain a note:

> If the initial navigation fails (network error, bad URL, etc.) the browser instance is **not** destroyed. The result includes a `nav_warning` key with the error message. The instance is live and `browser_navigate` can be called to retry or go to a different URL.

---

## 5. README Port + `.octowright` Scaffold Caching Note

**README.md** — three occurrences of `8765` replaced with `6286`:
- Line ~157: HTTP server description
- Line ~206: port-fallback description
- Line ~616: env var table default value

**`.octowright/config.yaml` scaffold** (`scaffold.py → write_project_config`) — add a comment block:

```yaml
# Note: octowright caches this file at daemon startup.
# Changes take effect after `octowright restart`.
```

---

## 6. Reliable New-Tab Redirect

**Problem:** The current `_make_new_tab_redirector` in `launch_pipeline.py` uses `asyncio.sleep(0.4)` then checks `page.url`. This is unreliable — sometimes Chrome hasn't finished navigating to `chrome://newtab/` yet, or the navigation from `chrome://newtab/` fails silently because it's a privileged URL.

**Fix:** Replace the fixed sleep + single-check pattern with a page `framenavigated` event listener that fires as soon as the URL is known, plus a fallback `domcontentloaded` check:

```python
async def _redirect() -> None:
    # Wait for either a navigated event or a timeout
    try:
        await new_page.wait_for_url(
            lambda url: url not in _BLANK_URLS or ...,
            timeout=1000
        )
    except Exception:
        pass
    # After settling, redirect if still on a blank/newtab URL
    if new_page.url in _BLANK_URLS or not new_page.url:
        await new_page.goto(get_default_url())
```

More precisely: use `page.wait_for_load_state("domcontentloaded", timeout=800)` to wait for the page to finish its initial navigation, then check the URL. This handles both fast and slow Chrome newtab navigations without a fixed sleep.

Also add `"chrome://newtab"` (without trailing slash) to `_BLANK_URLS` as a defensive measure.

For **Chromium only**: additionally pass `--new-tab-url=<get_default_url()>` as a Chromium launch argument so the OS-level Cmd+T produces the right URL natively, before Playwright even fires the `page` event. This is the belt-and-suspenders guarantee. Firefox and WebKit use only the event-based redirect.

## What Is NOT in Scope

- Macro pill changes (Alt-click run history modal already fully implemented)
- Badge click without Alt (badge stays click-through in normal use)
- Any new env vars beyond `OCTOWRIGHT_BADGE_OPACITY`
- Visual changes to the dashboard

---

## Test Coverage

- `tests/test_visuals.py` (or equivalent): assert all 8 position keys are in `_BADGE_POSITIONS`; assert center positions include `transform` field
- `tests/test_http_new_tab.py` / badge injection tests: assert `__OPACITY__` is substituted; assert `__DASHBOARD_URL__` and `__INSTANCE_ID__` are present when badge popup is enabled
- Existing badge injection tests: verify no regressions on `__TAG__`, `__COLOR__`, `__POS__` substitution
- README test: `grep -c "8765" README.md` returns 0 (can be a lint check)
- Scaffold test: assert generated config contains the caching comment
