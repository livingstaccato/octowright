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
    // Tracks the active outside-click listener so closeOverlay() can remove it
    // no matter which path closed the popup (outside click, Escape, badge
    // re-click). Without this, every open/close that isn't via an outside click
    // leaks a listener that keeps firing on subsequent clicks.
    let outsideClickListener = null;

    function getBadge() { return document.getElementById(BADGE_ID); }
    function getOverlay() { return document.getElementById(OVERLAY_ID); }

    function closeOverlay() {
        const ov = getOverlay();
        if (ov) ov.remove();
        const b = getBadge();
        if (b) { b.style.opacity = String(OPACITY); b.style.cursor = "default"; }
        if (outsideClickListener) {
            document.removeEventListener("click", outsideClickListener);
            outsideClickListener = null;
        }
    }

    function openOverlay() {
        if (getOverlay()) return;
        const isRight = POS.horizontal === "right" && !POS.h_offset;

        // Mirror the badge's centering transform so a center-anchored popup
        // sits centered on the same axis instead of extending off to one side.
        const transforms = [];
        if (POS.h_offset === "50%") transforms.push("translateX(-50%)");
        if (POS.v_offset === "50%") transforms.push("translateY(-50%)");

        const ov = document.createElement("div");
        ov.id = OVERLAY_ID;
        Object.assign(ov.style, {
            position: "fixed", zIndex: "2147483646",
            [POS.vertical]: POS.v_offset || "44px",
            [isRight ? "right" : "left"]: isRight ? "8px" : (POS.h_offset || "8px"),
            transform: transforms.join(" "),
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
        const rows = [["id", INSTANCE_ID], ["url", url]];
        if (TAG) rows.splice(1, 0, ["label", TAG.replace(/^\S+\s+/, "")]);

        const title = document.createElement("div");
        title.textContent = "session info";
        Object.assign(title.style, { fontSize: "9px", color: "#555", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: "7px" });
        ov.appendChild(title);

        rows.forEach(([k, v]) => {
            const row = document.createElement("div");
            Object.assign(row.style, { display: "flex", justifyContent: "space-between", gap: "14px", padding: "2px 0", borderBottom: "1px solid rgba(255,255,255,0.05)" });
            const kEl = document.createElement("span");
            kEl.textContent = k; kEl.style.color = "#666";
            const vEl = document.createElement("span");
            vEl.textContent = v;
            Object.assign(vEl.style, { color: "#c8d3f5", fontWeight: "bold", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "150px" });
            row.appendChild(kEl); row.appendChild(vEl); ov.appendChild(row);
        });

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

        setTimeout(() => {
            outsideClickListener = (e) => {
                if (!ov.contains(e.target) && e.target !== getBadge()) {
                    closeOverlay();
                }
            };
            document.addEventListener("click", outsideClickListener);
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
