(() => {
    if (window.top !== window.self) return;
    const ROOT_ID = "__octowright_macro_status__";
    const CHIP_TAG = __ID_TAG__;
    const CHIP_COLOR = __ID_COLOR__;
    // Auto-hide guards against orphaned pills if a macro crashes before the
    // run_macro `finally` fires. While a macro is actively pushing or after
    // a `done` push, this timer is suspended.
    const AUTO_HIDE_MS = 4000;
    // Modifier that flips the pill from click-through to clickable. Using
    // Alt (Option on Mac) keeps it out of the way of normal page shortcuts
    // while still being a single-key gesture.
    const CLICK_MODIFIER = "altKey";

    const MODAL_ID = "__octowright_macro_modal__";

    let hideTimer = null;
    let tickTimer = null;
    let startTs = null;
    let frozenMs = null;     // when set, elapsed renders this fixed value
    let modifierActive = false;
    // Per-run history. Each run is { startTs, endTs, entries: [{ts, text, kind}] }.
    // `currentRun` is the in-progress (or just-completed) run; promoted to
    // `lastRun` when the next start arrives. The modal shows currentRun ?? lastRun.
    let currentRun = null;
    let lastRun = null;
    // The pill's contents live in a CLOSED shadow root so page automation cannot
    // see them. Closed shadow roots are not exposed via host.shadowRoot, so we
    // hold the reference here for our own queries.
    let pillShadow = null;

    const fmtElapsed = (ms) => {
        if (ms == null) return "";
        const sec = ms / 1000;
        if (sec < 10) return sec.toFixed(1) + "s";
        if (sec < 60) return Math.round(sec) + "s";
        const m = Math.floor(sec / 60);
        const s = Math.round(sec % 60);
        return m + "m" + String(s).padStart(2, "0") + "s";
    };

    const applyInteractive = () => {
        const root = document.getElementById(ROOT_ID);
        if (!root) return;
        if (modifierActive) {
            root.style.pointerEvents = "auto";
            root.style.cursor = "pointer";
            root.style.outline = "1px solid rgba(255, 255, 255, 0.4)";
            root.style.outlineOffset = "1px";
        } else {
            root.style.pointerEvents = "none";
            root.style.cursor = "";
            root.style.outline = "";
            root.style.outlineOffset = "";
        }
    };

    const onModalKey = (e) => {
        if (e.key === "Escape") closeModal();
    };

    const closeModal = () => {
        const m = document.getElementById(MODAL_ID);
        if (m) m.remove();
        document.removeEventListener("keydown", onModalKey, true);
    };

    const styleRow = (row, kind) => {
        Object.assign(row.style, {
            display: "grid",
            gridTemplateColumns: "60px 70px 1fr",
            gap: "10px",
            padding: "3px 6px",
            borderRadius: "4px",
            opacity: kind === "start" || kind === "done" || kind === "failed" ? "0.85" : "1",
        });
    };

    const kindColor = (kind) => {
        if (kind === "done") return "rgba(80, 200, 120, 0.85)";
        if (kind === "failed") return "rgba(255, 110, 110, 0.85)";
        if (kind === "start") return "rgba(140, 180, 255, 0.85)";
        return "rgba(255, 255, 255, 0.45)";
    };

    const openModal = () => {
        const run = currentRun || lastRun;
        if (!run) return;
        closeModal();   // dedupe

        const overlay = document.createElement("div");
        overlay.id = MODAL_ID;
        Object.assign(overlay.style, {
            position: "fixed",
            inset: "0",
            zIndex: "2147483647",
            background: "rgba(0, 0, 0, 0.42)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            backdropFilter: "blur(3px)",
            WebkitBackdropFilter: "blur(3px)",
            opacity: "0",
            transition: "opacity 200ms ease",
            font: "12px ui-monospace, Menlo, Consolas, monospace",
            color: "rgba(255, 255, 255, 0.92)",
        });

        const card = document.createElement("div");
        Object.assign(card.style, {
            background: "rgba(20, 20, 24, 0.95)",
            border: "1px solid rgba(255, 255, 255, 0.08)",
            borderRadius: "12px",
            padding: "14px 16px 12px",
            minWidth: "440px",
            maxWidth: "min(760px, 92vw)",
            maxHeight: "72vh",
            boxShadow: "0 16px 50px rgba(0, 0, 0, 0.55)",
            display: "flex",
            flexDirection: "column",
            gap: "10px",
            position: "relative",
        });

        // ---- Header: chip + title + close ----
        const header = document.createElement("div");
        Object.assign(header.style, {
            display: "flex",
            alignItems: "center",
            gap: "10px",
            paddingBottom: "10px",
            borderBottom: "1px solid rgba(255, 255, 255, 0.08)",
        });
        const chip = document.createElement("span");
        chip.textContent = CHIP_TAG;
        Object.assign(chip.style, {
            background: CHIP_COLOR,
            color: "white",
            padding: "2px 8px",
            borderRadius: "8px",
            fontWeight: "600",
            textShadow: "0 0 2px rgba(0,0,0,0.7)",
            flex: "0 0 auto",
        });
        const totalMs = (run.endTs != null ? run.endTs : performance.now()) - run.startTs;
        const status = run.endTs != null ? (run.failed ? "failed" : "completed") : "running";
        const title = document.createElement("span");
        title.textContent = `macro run · ${run.entries.length} pushes · ${status} · ${fmtElapsed(totalMs)}`;
        Object.assign(title.style, { flex: "1", opacity: "0.85" });
        const closeBtn = document.createElement("button");
        closeBtn.textContent = "×";  // multiplication sign as the close glyph
        closeBtn.setAttribute("aria-label", "Close");
        Object.assign(closeBtn.style, {
            background: "transparent",
            border: "0",
            cursor: "pointer",
            font: "20px/1 ui-sans-serif, system-ui, sans-serif",
            color: "rgba(255, 255, 255, 0.5)",
            padding: "0 6px",
            borderRadius: "6px",
            outline: "none",
        });
        closeBtn.addEventListener("mouseenter", () => { closeBtn.style.color = "rgba(255,255,255,0.95)"; });
        closeBtn.addEventListener("mouseleave", () => { closeBtn.style.color = "rgba(255,255,255,0.5)"; });
        closeBtn.addEventListener("click", (e) => { e.stopPropagation(); closeModal(); });
        header.append(chip, title, closeBtn);

        // ---- Body: scrollable history table ----
        const body = document.createElement("div");
        Object.assign(body.style, {
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: "2px",
            padding: "0 2px",
            maxHeight: "52vh",
        });
        for (const entry of run.entries) {
            const row = document.createElement("div");
            styleRow(row, entry.kind);
            const tCol = document.createElement("span");
            tCol.textContent = fmtElapsed(entry.ts - run.startTs);
            Object.assign(tCol.style, {
                color: "rgba(255,255,255,0.45)",
                fontVariantNumeric: "tabular-nums",
                textAlign: "right",
            });
            const kindCol = document.createElement("span");
            kindCol.textContent = entry.kind;
            kindCol.style.color = kindColor(entry.kind);
            const textCol = document.createElement("span");
            textCol.textContent = entry.text || "";
            Object.assign(textCol.style, {
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
            });
            row.append(tCol, kindCol, textCol);
            body.append(row);
        }

        // ---- Footer: dismissal hint ----
        const footer = document.createElement("div");
        footer.textContent = "Esc or click outside to close · Alt+click pill to reopen";
        Object.assign(footer.style, {
            fontSize: "10px",
            color: "rgba(255, 255, 255, 0.4)",
            textAlign: "right",
            paddingTop: "8px",
            borderTop: "1px solid rgba(255, 255, 255, 0.08)",
        });

        card.append(header, body, footer);
        overlay.append(card);

        // Backdrop click closes; clicks inside the card do not.
        overlay.addEventListener("click", (e) => { if (e.target === overlay) closeModal(); });

        document.body.append(overlay);
        document.addEventListener("keydown", onModalKey, true);

        // Fade in next frame.
        requestAnimationFrame(() => { overlay.style.opacity = "1"; });
        // Auto-scroll to the bottom so the latest action is visible.
        body.scrollTop = body.scrollHeight;
    };

    const onClick = (event) => {
        // Stop the click from reaching the page underneath. With
        // pointer-events:auto the click would otherwise bubble.
        event.stopPropagation();
        event.preventDefault();
        openModal();
    };

    const build = () => {
        let root = document.getElementById(ROOT_ID);
        if (root) return root;
        if (!document.body) return null;
        root = document.createElement("div");
        root.id = ROOT_ID;
        Object.assign(root.style, {
            position: "fixed",
            left: "50%",
            bottom: "14px",
            transform: "translateX(-50%)",
            zIndex: "2147483646",
            display: "flex",
            alignItems: "center",
            gap: "8px",
            padding: "4px 6px 4px 4px",
            background: "rgba(20, 20, 24, 0.42)",
            color: "rgba(255, 255, 255, 0.88)",
            font: "11px ui-monospace, Menlo, Consolas, monospace",
            borderRadius: "12px",
            backdropFilter: "blur(6px)",
            WebkitBackdropFilter: "blur(6px)",
            boxShadow: "0 1px 4px rgba(0, 0, 0, 0.18)",
            pointerEvents: "none",
            userSelect: "none",
            opacity: "0",
            transition: "opacity 240ms ease, background 220ms ease",
            maxWidth: "80vw",
            letterSpacing: "0.02em",
        });

        const chip = document.createElement("span");
        chip.dataset.role = "chip";
        chip.textContent = CHIP_TAG;
        Object.assign(chip.style, {
            background: CHIP_COLOR,
            color: "white",
            padding: "2px 7px",
            borderRadius: "8px",
            fontWeight: "600",
            textShadow: "0 0 2px rgba(0,0,0,0.7)",
            flex: "0 0 auto",
        });

        const elapsed = document.createElement("span");
        elapsed.dataset.role = "elapsed";
        Object.assign(elapsed.style, {
            color: "rgba(255, 255, 255, 0.65)",
            flex: "0 0 auto",
            minWidth: "28px",
            textAlign: "right",
            fontVariantNumeric: "tabular-nums",
        });

        const sep = document.createElement("span");
        sep.dataset.role = "sep";
        sep.textContent = "·";
        Object.assign(sep.style, {
            color: "rgba(255, 255, 255, 0.35)",
            flex: "0 0 auto",
        });

        const label = document.createElement("span");
        label.dataset.role = "label";
        Object.assign(label.style, {
            flex: "1 1 auto",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            paddingRight: "4px",
        });

        // Render contents inside a CLOSED shadow root. Playwright (and other
        // automation) cannot pierce a closed shadow tree, so the label — which
        // echoes the action text, e.g. "click_by text=Place order" — never
        // becomes a second get_by_text()/get_by_role() match for the page's own
        // elements during replay. Must be "closed": OPEN shadow roots ARE pierced.
        pillShadow = root.attachShadow({ mode: "closed" });
        pillShadow.append(chip, elapsed, sep, label);
        root.addEventListener("click", onClick);
        document.body.appendChild(root);
        applyInteractive();
        return root;
    };

    const stopTicking = () => {
        if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
    };

    const renderElapsed = () => {
        if (!pillShadow) return;
        const el = pillShadow.querySelector('[data-role="elapsed"]');
        if (!el) return;
        if (frozenMs != null) {
            el.textContent = fmtElapsed(frozenMs);
        } else if (startTs != null) {
            el.textContent = fmtElapsed(performance.now() - startTs);
        }
    };

    const hide = () => {
        const root = document.getElementById(ROOT_ID);
        if (root) root.style.opacity = "0";
        stopTicking();
    };

    const recordEntry = (payload, kind) => {
        if (!currentRun) return;
        // Cap entries so a runaway loop can't blow up memory.
        if (currentRun.entries.length >= 2000) return;
        currentRun.entries.push({
            ts: performance.now(),
            text: payload && payload.text != null ? String(payload.text) : "",
            kind: kind,
        });
    };

    const show = (payload) => {
        const root = build();
        if (!root) {
            document.addEventListener("DOMContentLoaded", () => show(payload), { once: true });
            return;
        }
        const isStart = payload && payload.start === true;
        const isDone = payload && payload.done === true;
        const isFailed = isDone && payload && /\| failed/.test(String(payload.text || ""));

        // Start a fresh elapsed counter on explicit start, on done following
        // a hidden pill (no prior session), or on first show after a hide.
        // Subsequent text-only pushes preserve the running clock.
        if (isStart) {
            // Archive any previous run before starting a new one.
            if (currentRun) lastRun = currentRun;
            currentRun = { startTs: performance.now(), endTs: null, entries: [], failed: false };
            startTs = currentRun.startTs;
            frozenMs = null;
        } else if (startTs == null) {
            // First push without an explicit start — treat it as the start.
            currentRun = { startTs: performance.now(), endTs: null, entries: [], failed: false };
            startTs = currentRun.startTs;
            frozenMs = null;
        }

        // Record this push in the run history before tweaking other state.
        recordEntry(payload, isStart ? "start" : (isDone ? (isFailed ? "failed" : "done") : "action"));

        // `done` freezes elapsed at the moment it arrives so the user sees
        // total runtime, not a clock that keeps ticking after completion.
        if (isDone) {
            frozenMs = performance.now() - (startTs == null ? performance.now() : startTs);
            if (currentRun) {
                currentRun.endTs = performance.now();
                currentRun.failed = isFailed;
            }
        } else if (isStart) {
            frozenMs = null;
        }

        const label = pillShadow ? pillShadow.querySelector('[data-role="label"]') : null;
        if (label) label.textContent = String(payload.text == null ? "" : payload.text);
        renderElapsed();

        // 0.7 keeps it readable but visibly secondary to the page itself.
        root.style.opacity = "0.7";

        // Reset auto-hide on every push EXCEPT done — done means "stay open
        // until the next macro start or an explicit hide".
        if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
        if (!isDone) hideTimer = setTimeout(() => { hide(); hideTimer = null; }, AUTO_HIDE_MS);

        // Stop the ticker on done (frozen value); otherwise keep it ticking.
        if (isDone) {
            stopTicking();
        } else if (!tickTimer) {
            tickTimer = setInterval(renderElapsed, 100);
        }
    };

    // ---- Modifier-key listening: flips pointer-events on the pill ----
    const onKey = (event) => {
        const want = !!event[CLICK_MODIFIER];
        if (want === modifierActive) return;
        modifierActive = want;
        applyInteractive();
    };
    const onBlur = () => {
        if (!modifierActive) return;
        modifierActive = false;
        applyInteractive();
    };
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("keyup", onKey, true);
    window.addEventListener("blur", onBlur);

    window.__octowright_macro_status = (payload) => {
        try {
            if (!payload || payload.visible === false) {
                if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
                hide();
                if (currentRun && currentRun.endTs == null) {
                    currentRun.endTs = performance.now();
                }
                if (currentRun) lastRun = currentRun;
                currentRun = null;
                startTs = null;
                frozenMs = null;
                return;
            }
            show(payload);
        } catch (_) {}
    };
})();
