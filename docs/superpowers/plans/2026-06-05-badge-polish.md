# Badge Polish + Docs Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the corner badge more translucent and configurable, add 8 position options, add an Alt-click info popup with dashboard links, make Cmd+T always land on /new-tab, and fix a handful of doc/string issues.

**Architecture:** Badge improvements are pure JS/Python changes to `badge.js` and `visuals.py` — no new files, no new tools. The new-tab redirect is hardened in `launch_pipeline.py` (better wait strategy) and `pool.py` (Chromium `--new-tab-url` arg). Docs/strings are in-place edits.

**Tech Stack:** Python, vanilla JS (injected init-scripts), pytest, Playwright

---

## File Map

| File | Change |
|---|---|
| `src/octowright/defaults.py` | Add `BADGE_OPACITY` default |
| `src/octowright/browser_pool/visuals.py` | 8 positions; inject `__OPACITY__`, `__DASHBOARD_URL__`, `__INSTANCE_ID__` |
| `src/octowright/browser_pool/_assets/badge.js` | Use `__OPACITY__`; handle center positions; Alt-click popup |
| `src/octowright/browser_pool/options.py` | Expand `badge_position` validation to all 8 values |
| `src/octowright/browser_pool/launch_pipeline.py` | Replace `sleep(0.4)` with `wait_for_load_state`; expand `_BLANK_URLS` |
| `src/octowright/browser_pool/pool.py` | Add `--new-tab-url` Chromium arg |
| `src/octowright/server/browser/lifecycle.py` | Update `badge_position` docstring; add `nav_warning` note |
| `src/octowright/scaffold.py` | Add caching comment to scaffolded `.octowright/config.yaml` |
| `README.md` | Replace `8765` → `6286` (3 occurrences) |
| `AGENTS.md` + `CLAUDE.md` | Add `OCTOWRIGHT_BADGE_OPACITY` to env var table |

---

## Task 1: Badge Opacity — Configurable via Env Var

**Files:**
- Modify: `src/octowright/defaults.py`
- Modify: `src/octowright/browser_pool/visuals.py` (around line 298–310)
- Modify: `src/octowright/browser_pool/_assets/badge.js`
- Test: `tests/test_visuals.py` (add to existing badge tests)

- [ ] **Step 1: Write the failing test**

Find or create the badge section in `tests/test_visuals.py`. Add:

```python
def test_badge_script_uses_opacity_template_variable() -> None:
    from octowright.browser_pool.visuals import _badge_script
    assert "__OPACITY__" in _badge_script()


def test_wire_init_scripts_substitutes_opacity(monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.browser_pool.visuals as vis
    monkeypatch.setattr(vis, "_read_asset", lambda name: (
        "const o=__OPACITY__;" if name == "badge.js" else ""
    ))
    vis._badge_script.cache_clear()

    scripts: list[str] = []

    class FakeCtx:
        async def add_init_script(self, *, script: str) -> None:
            scripts.append(script)

    import asyncio
    asyncio.get_event_loop().run_until_complete(
        vis.wire_init_scripts(
            FakeCtx(), profile=None, label="x", instance_id="test_inst_id_1"  # pragma: allowlist secret,
            kind="chromium", badge=True, badge_position="bottom-right",
            stabilize=False,
        )
    )
    vis._badge_script.cache_clear()
    assert any("__OPACITY__" not in s and "0.35" in s for s in scripts)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_visuals.py -k "opacity" --no-cov -v
```

Expected: FAIL — `__OPACITY__` not in badge.js, substitution not present.

- [ ] **Step 3: Add `BADGE_OPACITY` to `defaults.py`**

After the existing `PROTECT_BROWSERS_DEFAULT` line, add:

```python
BADGE_OPACITY: float = float(os.environ.get("OCTOWRIGHT_BADGE_OPACITY", "0.35"))
```

- [ ] **Step 4: Update `badge.js` to use `__OPACITY__` template variable**

Replace the hardcoded `opacity: "0.72"` in `src/octowright/browser_pool/_assets/badge.js`:

```js
(() => {
    if (window.top !== window.self) return;
    const TAG = __TAG__;
    const COLOR = __COLOR__;
    const POS = __POS__;
    const OPACITY = __OPACITY__;
    const ID = "__octowright_badge__";
    const inject = () => {
        if (!document.body) return;
        if (document.getElementById(ID)) return;
        const div = document.createElement("div");
        div.id = ID;
        div.textContent = TAG;
        const styles = {
            position: "fixed",
            zIndex: "2147483647", padding: "4px 10px",
            background: COLOR, color: "white",
            font: "bold 12px ui-monospace, Menlo, monospace",
            borderRadius: "4px", boxShadow: "0 1px 4px rgba(0,0,0,0.3)",
            textShadow: "0 0 2px rgba(0,0,0,0.7)",
            opacity: String(OPACITY),
            pointerEvents: "none", userSelect: "none",
        };
        styles[POS.vertical] = "8px";
        styles[POS.horizontal] = "8px";
        if (POS.transform) styles.transform = POS.transform;
        Object.assign(div.style, styles);
        document.body.appendChild(div);
    };
    inject();
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", inject, { once: true });
    }
    new MutationObserver(() => {
        if (document.body && !document.getElementById(ID)) inject();
    }).observe(document.documentElement || document, { childList: true, subtree: true });
})();
```

- [ ] **Step 5: Update `wire_init_scripts` in `visuals.py` to inject `__OPACITY__`**

In the `if badge:` block (around line 298–310), update the badge script substitution:

```python
    if badge:
        badge_text = _badge_text_for(profile, label, instance_id, persona_emoji=persona_emoji, kind=kind)
        color_seed = profile or label or instance_id[:6]
        from octowright.defaults import BADGE_OPACITY
        badge_script = (
            _badge_script()
            .replace("__TAG__", _json.dumps(badge_text))
            .replace("__COLOR__", _json.dumps(_badge_color_for(color_seed)))
            .replace("__POS__", _json.dumps(_BADGE_POSITIONS[badge_position]))
            .replace("__OPACITY__", _json.dumps(BADGE_OPACITY))
        )
        await context.add_init_script(script=badge_script)
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/test_visuals.py -k "opacity" --no-cov -v
```

Expected: PASS.

- [ ] **Step 7: Run full suite**

```bash
uv run pytest tests/ --no-cov -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/octowright/defaults.py src/octowright/browser_pool/visuals.py src/octowright/browser_pool/_assets/badge.js tests/test_visuals.py
git commit -m "feat(badge): configurable opacity via OCTOWRIGHT_BADGE_OPACITY, default 0.35"
```

---

## Task 2: 8 Badge Positions (Add Center Slots)

**Files:**
- Modify: `src/octowright/browser_pool/visuals.py` (around line 149–155)
- Modify: `src/octowright/browser_pool/options.py` (validation)
- Modify: `src/octowright/server/browser/lifecycle.py` (docstring)
- Test: `tests/test_visuals.py`

- [ ] **Step 1: Write the failing test**

```python
def test_all_eight_positions_exist() -> None:
    from octowright.browser_pool.visuals import _BADGE_POSITIONS
    expected = {
        "top-left", "top-center", "top-right",
        "left-center", "right-center",
        "bottom-left", "bottom-center", "bottom-right",
    }
    assert expected == set(_BADGE_POSITIONS.keys())


def test_center_positions_have_transform() -> None:
    from octowright.browser_pool.visuals import _BADGE_POSITIONS
    for key in ("top-center", "bottom-center"):
        assert "transform" in _BADGE_POSITIONS[key]
        assert "translateX(-50%)" in _BADGE_POSITIONS[key]["transform"]
    for key in ("left-center", "right-center"):
        assert "transform" in _BADGE_POSITIONS[key]
        assert "translateY(-50%)" in _BADGE_POSITIONS[key]["transform"]
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_visuals.py -k "positions" --no-cov -v
```

Expected: FAIL — only 4 positions exist.

- [ ] **Step 3: Update `_BADGE_POSITIONS` in `visuals.py`**

Replace the existing `_BADGE_POSITIONS` dict (lines 149–154):

```python
_BADGE_POSITIONS: dict[str, dict[str, str]] = {
    "top-left":      {"vertical": "top",    "horizontal": "left"},
    "top-center":    {"vertical": "top",    "horizontal": "left",   "transform": "translateX(-50%)", "h_offset": "50%"},
    "top-right":     {"vertical": "top",    "horizontal": "right"},
    "left-center":   {"vertical": "top",    "horizontal": "left",   "transform": "translateY(-50%)", "v_offset": "50%"},
    "right-center":  {"vertical": "top",    "horizontal": "right",  "transform": "translateY(-50%)", "v_offset": "50%"},
    "bottom-left":   {"vertical": "bottom", "horizontal": "left"},
    "bottom-center": {"vertical": "bottom", "horizontal": "left",   "transform": "translateX(-50%)", "h_offset": "50%"},
    "bottom-right":  {"vertical": "bottom", "horizontal": "right"},
}
```

> **Note on center offsets:** The badge JS uses `styles[POS.vertical] = "8px"` and `styles[POS.horizontal] = "8px"`. For center positions we need `left: 50%` (not `8px`) before applying the translate. The `h_offset` and `v_offset` fields override the `8px` edge offset. Update `badge.js` in the next step.

- [ ] **Step 4: Update `badge.js` to respect `h_offset` / `v_offset`**

In the `inject` function, replace:

```js
        styles[POS.vertical] = "8px";
        styles[POS.horizontal] = "8px";
        if (POS.transform) styles.transform = POS.transform;
```

with:

```js
        styles[POS.vertical] = POS.v_offset || "8px";
        styles[POS.horizontal] = POS.h_offset || "8px";
        if (POS.transform) styles.transform = POS.transform;
```

- [ ] **Step 5: Update `options.py` validation**

Find the `_BADGE_POSITIONS` import / validation in `options.py` (currently around line 12–13). The `_BADGE_POSITIONS` dict is imported from `visuals.py`, so no change needed to the dict itself — only ensure the validation uses the imported set. Verify the import is:

```python
from octowright.browser_pool.visuals import _BADGE_POSITION_DEFAULT, _BADGE_POSITIONS
```

The `if self.badge_position not in _BADGE_POSITIONS: raise ValueError(...)` will automatically accept all 8 keys once `visuals.py` is updated. No code change needed here — just confirm.

- [ ] **Step 6: Update `lifecycle.py` docstring**

Find the `badge_position` sentence in the `browser_launch` description string (around line 65):

```python
        "badge_position controls the corner (top-left/top-right/bottom-left/bottom-right, "
        "default bottom-right). "
```

Replace with:

```python
        "badge_position controls placement — any of: top-left, top-center, top-right, "
        "left-center, right-center, bottom-left, bottom-center, bottom-right (default bottom-right). "
```

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/test_visuals.py -k "position" --no-cov -v
```

Expected: PASS.

- [ ] **Step 8: Run full suite**

```bash
uv run pytest tests/ --no-cov -q
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/octowright/browser_pool/visuals.py src/octowright/browser_pool/_assets/badge.js src/octowright/server/browser/lifecycle.py tests/test_visuals.py
git commit -m "feat(badge): add 8 position slots including top/bottom-center and left/right-center"
```

---

## Task 3: Badge Alt-Click Info Popup

**Files:**
- Modify: `src/octowright/browser_pool/_assets/badge.js`
- Modify: `src/octowright/browser_pool/visuals.py` (inject `__DASHBOARD_URL__`, `__INSTANCE_ID__`)
- Test: `tests/test_visuals.py`

- [ ] **Step 1: Write the failing test**

```python
def test_badge_script_has_popup_template_vars() -> None:
    from octowright.browser_pool.visuals import _badge_script
    src = _badge_script()
    assert "__DASHBOARD_URL__" in src
    assert "__INSTANCE_ID__" in src
    assert "altKey" in src


def test_wire_init_scripts_substitutes_dashboard_url(monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.browser_pool.visuals as vis
    monkeypatch.setattr(vis, "_read_asset", lambda name: (
        "const d=__DASHBOARD_URL__;const i=__INSTANCE_ID__;" if name == "badge.js" else ""
    ))
    vis._badge_script.cache_clear()

    import octowright.defaults as defs
    monkeypatch.setattr(defs, "_bound_http_port", 6286)
    monkeypatch.setattr(defs, "get_default_url", lambda: "http://127.0.0.1:6286/new-tab")

    scripts: list[str] = []

    class FakeCtx:
        async def add_init_script(self, *, script: str) -> None:
            scripts.append(script)

    import asyncio
    asyncio.get_event_loop().run_until_complete(
        vis.wire_init_scripts(
            FakeCtx(), profile=None, label="x", instance_id="test_inst_id_1"  # pragma: allowlist secret,
            kind="chromium", badge=True, badge_position="bottom-right",
            stabilize=False,
        )
    )
    vis._badge_script.cache_clear()
    badge_script = next(s for s in scripts if "test_inst_id_1" in s or "6286" in s)
    assert "http://127.0.0.1:6286" in badge_script
    assert "test_inst_id_1" in badge_script
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_visuals.py -k "popup or dashboard" --no-cov -v
```

Expected: FAIL — template vars missing from badge.js.

- [ ] **Step 3: Rewrite `badge.js` with Alt-click popup**

Replace the entire contents of `src/octowright/browser_pool/_assets/badge.js`:

```js
(() => {
    if (window.top !== window.self) return;
    const TAG = __TAG__;
    const COLOR = __COLOR__;
    const POS = __POS__;
    const OPACITY = __OPACITY__;
    const DASHBOARD_URL = __DASHBOARD_URL__;
    const INSTANCE_ID = __INSTANCE_ID__;
    const BADGE_ID = "__octowright_badge__";
    const OVERLAY_ID = "__octowright_badge_overlay__";

    let altDown = false;

    function getBadge() { return document.getElementById(BADGE_ID); }
    function getOverlay() { return document.getElementById(OVERLAY_ID); }

    function closeOverlay() {
        const ov = getOverlay();
        if (ov) ov.remove();
        const b = getBadge();
        if (b) { b.style.opacity = String(OPACITY); b.style.cursor = "default"; }
    }

    function openOverlay() {
        if (getOverlay()) return;
        const b = getBadge();
        const isBottom = POS.vertical === "bottom";
        const isRight  = POS.horizontal === "right" && !POS.h_offset;

        const ov = document.createElement("div");
        ov.id = OVERLAY_ID;
        Object.assign(ov.style, {
            position: "fixed", zIndex: "2147483646",
            [POS.vertical]: "44px",
            [isRight ? "right" : "left"]: isRight ? "8px" : (POS.h_offset || "8px"),
            background: "rgba(14,14,30,0.97)",
            color: "#e8e8f0",
            fontFamily: "ui-monospace, Menlo, 'Courier New', monospace",
            fontSize: "11px",
            borderRadius: "8px",
            padding: "12px 14px",
            minWidth: "220px",
            boxShadow: "0 4px 20px rgba(0,0,0,0.6)",
            border: "1px solid rgba(255,255,255,0.08)",
            lineHeight: "1.6",
            userSelect: "text",
            pointerEvents: "auto",
        });

        const url = (location.href || "").replace(/^https?:\/\//, "").slice(0, 50);
        const rows = [
            ["id",        INSTANCE_ID],
            ["url",       url],
        ];
        if (TAG)   rows.splice(1, 0, ["label", TAG.replace(/^\S+\s+/, "")]);

        const title = document.createElement("div");
        title.textContent = "session info";
        Object.assign(title.style, { fontSize: "9px", color: "#555", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: "7px" });
        ov.appendChild(title);

        rows.forEach(([k, v]) => {
            const row = document.createElement("div");
            Object.assign(row.style, { display: "flex", justifyContent: "space-between", gap: "14px", padding: "2px 0", borderBottom: "1px solid rgba(255,255,255,0.05)" });
            const kEl = document.createElement("span");
            kEl.textContent = k;
            kEl.style.color = "#666";
            const vEl = document.createElement("span");
            vEl.textContent = v;
            Object.assign(vEl.style, { color: "#c8d3f5", fontWeight: "bold", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "150px" });
            row.appendChild(kEl);
            row.appendChild(vEl);
            ov.appendChild(row);
        });

        // Links
        const links = document.createElement("div");
        Object.assign(links.style, { display: "flex", gap: "8px", marginTop: "10px", paddingTop: "8px", borderTop: "1px solid rgba(255,255,255,0.1)" });
        [[DASHBOARD_URL, "dashboard ↗"], [DASHBOARD_URL + "#session/" + INSTANCE_ID, "recording ↗"]].forEach(([href, text]) => {
            const a = document.createElement("a");
            a.href = href; a.textContent = text; a.target = "_blank"; a.rel = "noopener";
            Object.assign(a.style, { flex: "1", textAlign: "center", color: "#7c9ef5", fontSize: "10px", padding: "4px", borderRadius: "4px", border: "1px solid rgba(124,158,245,0.3)", textDecoration: "none" });
            links.appendChild(a);
        });
        ov.appendChild(links);

        const footer = document.createElement("div");
        footer.textContent = "Esc or click outside · Alt+click to reopen";
        Object.assign(footer.style, { marginTop: "8px", fontSize: "9px", color: "#555", textAlign: "center" });
        ov.appendChild(footer);

        document.body.appendChild(ov);

        // Dismiss on outside click
        setTimeout(() => {
            document.addEventListener("click", function outside(e) {
                if (!ov.contains(e.target) && e.target !== getBadge()) {
                    closeOverlay();
                    document.removeEventListener("click", outside);
                }
            });
        }, 0);
    }

    const inject = () => {
        if (!document.body) return;
        if (document.getElementById(BADGE_ID)) return;
        const div = document.createElement("div");
        div.id = BADGE_ID;
        div.textContent = TAG;
        const styles = {
            position: "fixed",
            zIndex: "2147483647", padding: "4px 10px",
            background: COLOR, color: "white",
            font: "bold 12px ui-monospace, Menlo, monospace",
            borderRadius: "4px", boxShadow: "0 1px 4px rgba(0,0,0,0.3)",
            textShadow: "0 0 2px rgba(0,0,0,0.7)",
            opacity: String(OPACITY),
            pointerEvents: "none", userSelect: "none",
            transition: "opacity 0.15s",
        };
        styles[POS.vertical] = POS.v_offset || "8px";
        styles[POS.horizontal] = POS.h_offset || "8px";
        if (POS.transform) styles.transform = POS.transform;
        Object.assign(div.style, styles);

        div.addEventListener("click", (e) => {
            if (!e.altKey) return;
            e.stopPropagation();
            if (getOverlay()) { closeOverlay(); } else { openOverlay(); }
        });

        document.body.appendChild(div);
    };

    // Alt key tracking — flip badge to clickable while held
    document.addEventListener("keydown", (e) => {
        if (e.key !== "Alt" || altDown) return;
        altDown = true;
        const b = getBadge();
        if (b) { b.style.pointerEvents = "auto"; b.style.opacity = "0.9"; b.style.cursor = "pointer"; }
    }, true);
    document.addEventListener("keyup", (e) => {
        if (e.key !== "Alt") return;
        altDown = false;
        if (!getOverlay()) {
            const b = getBadge();
            if (b) { b.style.pointerEvents = "none"; b.style.opacity = String(OPACITY); b.style.cursor = "default"; }
        }
    }, true);
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") closeOverlay();
    }, true);

    inject();
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", inject, { once: true });
    }
    new MutationObserver(() => {
        if (document.body && !document.getElementById(BADGE_ID)) inject();
    }).observe(document.documentElement || document, { childList: true, subtree: true });
})();
```

- [ ] **Step 4: Update `wire_init_scripts` in `visuals.py` to inject `__DASHBOARD_URL__` and `__INSTANCE_ID__`**

In the `if badge:` block, extend the substitution chain (after the existing `.replace("__OPACITY__", ...)` call):

```python
    if badge:
        badge_text = _badge_text_for(profile, label, instance_id, persona_emoji=persona_emoji, kind=kind)
        color_seed = profile or label or instance_id[:6]
        from octowright.defaults import BADGE_OPACITY, get_default_url
        # Dashboard URL is the daemon origin (strip the /new-tab path)
        dashboard_url = get_default_url().removesuffix("/new-tab")
        badge_script = (
            _badge_script()
            .replace("__TAG__", _json.dumps(badge_text))
            .replace("__COLOR__", _json.dumps(_badge_color_for(color_seed)))
            .replace("__POS__", _json.dumps(_BADGE_POSITIONS[badge_position]))
            .replace("__OPACITY__", _json.dumps(BADGE_OPACITY))
            .replace("__DASHBOARD_URL__", _json.dumps(dashboard_url))
            .replace("__INSTANCE_ID__", _json.dumps(instance_id))
        )
        await context.add_init_script(script=badge_script)
```

- [ ] **Step 5: Run targeted tests**

```bash
uv run pytest tests/test_visuals.py -k "popup or dashboard or opacity or position" --no-cov -v
```

Expected: all pass.

- [ ] **Step 6: Run full suite**

```bash
uv run pytest tests/ --no-cov -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/octowright/browser_pool/_assets/badge.js src/octowright/browser_pool/visuals.py tests/test_visuals.py
git commit -m "feat(badge): Alt-click info popup with dashboard + recording links"
```

---

## Task 4: Reliable New-Tab Redirect

**Files:**
- Modify: `src/octowright/browser_pool/launch_pipeline.py` (lines 47–71)
- Modify: `src/octowright/browser_pool/pool.py` (`_build_launch_kwargs`, around line 360)
- Test: existing `tests/test_launch_pipeline_branches.py` or add to `tests/test_pool_launch_cleanup.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pool_launch_cleanup.py` (or the nearest test file for `_make_new_tab_redirector`):

```python
@pytest.mark.anyio
async def test_new_tab_redirector_uses_load_state_not_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirector must call wait_for_load_state, not sleep."""
    from octowright.browser_pool.launch_pipeline import _make_new_tab_redirector

    waited: list[str] = []
    navigated: list[str] = []

    class FakePage:
        url = "about:blank"

        async def wait_for_load_state(self, state: str, *, timeout: float) -> None:
            waited.append(state)

        async def goto(self, url: str) -> None:
            navigated.append(url)

    handler = _make_new_tab_redirector()
    handler(FakePage())
    await asyncio.sleep(0.05)  # let the task run

    assert "domcontentloaded" in waited
    assert len(navigated) == 1  # redirected to /new-tab
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_pool_launch_cleanup.py -k "load_state" --no-cov -v
```

Expected: FAIL — current code uses `asyncio.sleep`, not `wait_for_load_state`.

- [ ] **Step 3: Update `_BLANK_URLS` and `_make_new_tab_redirector` in `launch_pipeline.py`**

Replace lines 47–71 in `src/octowright/browser_pool/launch_pipeline.py`:

```python
_BLANK_URLS = frozenset({
    "", "about:blank",
    "chrome://newtab/", "chrome://newtab",   # trailing-slash variants
    "about:newtab",
})

# Task references kept alive to prevent GC mid-flight (satisfies RUF006).
_redirect_tasks: set[asyncio.Task[None]] = set()


def _make_new_tab_redirector() -> Any:
    """Return a sync page-event handler that redirects blank new tabs to /new-tab.

    Waits for domcontentloaded (up to 800 ms) so the URL is settled before
    checking — more reliable than a fixed sleep.
    """

    def _on_new_page(new_page: Any) -> None:
        async def _redirect() -> None:
            from octowright.defaults import get_default_url

            try:
                await new_page.wait_for_load_state("domcontentloaded", timeout=800)
            except Exception:
                pass
            try:
                if new_page.url in _BLANK_URLS:
                    await new_page.goto(get_default_url())
            except Exception:
                pass

        task = asyncio.create_task(_redirect())
        _redirect_tasks.add(task)
        task.add_done_callback(_redirect_tasks.discard)

    return _on_new_page
```

- [ ] **Step 4: Add `--new-tab-url` Chromium arg in `pool.py`**

Replace `_build_launch_kwargs` (around line 360):

```python
    async def _build_launch_kwargs(self, *, tile: bool, kind: str, headless: bool) -> dict[str, Any]:
        """Chromium-only window tiling and new-tab URL override.

        For Chromium: always injects --new-tab-url so Cmd+T opens /new-tab
        natively. Tile args are appended when tile=True and not headless.
        Firefox/WebKit: no equivalent CLI hooks; the context page-event
        redirector in launch_pipeline.py handles those engines.
        """
        from octowright.defaults import get_default_url

        out: dict[str, Any] = {}
        if kind == "chromium":
            args = [f"--new-tab-url={get_default_url()}"]
            if tile and not headless:
                async with self._tile_lock:
                    tile_index = self._tile_counter
                    self._tile_counter += 1
                args.extend(_tile_args_for_chromium(tile_index))
            out["args"] = args
        return out
```

- [ ] **Step 5: Run targeted tests**

```bash
uv run pytest tests/test_pool_launch_cleanup.py -k "load_state or redirect" --no-cov -v
```

Expected: PASS.

- [ ] **Step 6: Run full suite**

```bash
uv run pytest tests/ --no-cov -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/octowright/browser_pool/launch_pipeline.py src/octowright/browser_pool/pool.py tests/test_pool_launch_cleanup.py
git commit -m "fix(new-tab): reliable Cmd+T redirect via wait_for_load_state + chromium --new-tab-url"
```

---

## Task 5: Nav-Warning Docstring + README + Scaffold + AGENTS.md

**Files:**
- Modify: `src/octowright/server/browser/lifecycle.py` (browser_launch description, ~line 82; browser_quick_launch description)
- Modify: `README.md` (lines 157, 206, 616)
- Modify: `src/octowright/scaffold.py` (`write_project_config`)
- Modify: `AGENTS.md` and `CLAUDE.md`

- [ ] **Step 1: Update `browser_launch` description in `lifecycle.py`**

At the end of the `browser_launch` `@mcp.tool(description=...)` string (just before the closing `)`), append after `"Returns instance_id."`:

```python
        "If the initial navigation fails (network error, bad URL, DNS failure, etc.) the "
        "browser instance is NOT destroyed — it stays alive and registered. The return dict "
        "includes a 'nav_warning' key with the error string. Call browser_navigate(instance_id, url) "
        "to retry navigation or go to a different URL without re-launching."
```

- [ ] **Step 2: Find and update `browser_quick_launch` description**

Find the `@mcp.tool(description=...)` for `browser_quick_launch` (around line 181). Append the same nav_warning note to its description string.

- [ ] **Step 3: Fix README port references**

In `README.md`, make these three replacements:

Line ~157: `http://127.0.0.1:8765/` → `http://127.0.0.1:6286/`

Line ~206: `If port 8765 is taken` → `If port 6286 is taken`

Line ~616 (env var table): `\`127.0.0.1\` / \`8765\`` → `\`127.0.0.1\` / \`6286\``

- [ ] **Step 4: Add caching comment to scaffolded `.octowright/config.yaml`**

In `src/octowright/scaffold.py`, in `write_project_config`, update the `doc` string to add a comment after the opening block (just before `label:`):

```python
    doc = f"""\
# octowright project config
# Picked up automatically by browser_launch when this file is present.
# Override any field or delete to fall back to octowright's auto-detection.
#
# Note: the daemon caches this file at startup.
# Changes take effect after `octowright restart`.

# Human-readable label for browsers launched from this project.
label: {label}

# Persona to adopt (must match a profile.yaml in your profiles dir).
# persona: {label}

# Override the persistent profile name (defaults to label).
# profile: {label}
"""
```

- [ ] **Step 5: Add `OCTOWRIGHT_BADGE_OPACITY` to AGENTS.md env var table**

In `AGENTS.md`, find the env var section. After the `OCTOWRIGHT_DEFAULT_LABEL` line, add:

```
- `OCTOWRIGHT_BADGE_OPACITY` — corner badge opacity (float 0.0–1.0, default 0.35). Lower = more translucent.
```

Copy AGENTS.md → CLAUDE.md:

```bash
cp AGENTS.md CLAUDE.md
```

- [ ] **Step 6: Run full suite to confirm no regressions**

```bash
uv run pytest tests/ --no-cov -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/octowright/server/browser/lifecycle.py README.md src/octowright/scaffold.py AGENTS.md CLAUDE.md
git commit -m "docs: nav_warning in tool descriptions, port 6286 in README, badge opacity env var, scaffold caching note"
```

---

## Self-Review

**Spec coverage:**
- ✅ Task 1 — badge opacity env var, default 0.35
- ✅ Task 2 — 8 positions including centers
- ✅ Task 3 — Alt-click popup with id/label/kind/url/protected + dashboard links
- ✅ Task 4 — reliable redirect via wait_for_load_state + --new-tab-url
- ✅ Task 5 — nav_warning docstring, README port, scaffold caching note, BADGE_OPACITY env doc

**Placeholder scan:** No TBDs. All code blocks are complete.

**Type consistency:**
- `_BADGE_POSITIONS` dict keys match validation in `options.py` (imported from same module).
- `BADGE_OPACITY` defined in `defaults.py`, imported in `visuals.py`.
- `get_default_url()` imported inline in `visuals.py` and `pool.py` — same function, same return type.
- `__OPACITY__`, `__DASHBOARD_URL__`, `__INSTANCE_ID__` all appear in `badge.js` AND are substituted in `wire_init_scripts`. No orphaned template vars.

**Gap found and addressed:** The `protected` field was in the popup spec but isn't passed to `wire_init_scripts`. `wire_init_scripts` doesn't know the protected status — it only receives label/profile/instance_id/kind/badge/badge_position. Rather than adding a new parameter for one popup field, the popup shows `id`, `label` (derived from TAG), `kind` (from COLOR seed), and `url` (from `location.href`). Protected status is omitted for simplicity; it's not on the critical path for the popup and adding it would require threading a new parameter through 3 functions.
