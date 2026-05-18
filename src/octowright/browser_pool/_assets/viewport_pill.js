(() => {
    if (window.top !== window.self) return;

    const ROOT_ID = "__octowright_viewport_status__";
    const MODAL_ID = "__octowright_viewport_modal__";
    const INITIAL = __VIEWPORT_INFO__;
    const CLICK_MODIFIER = "altKey";
    const ALT_HOLD_MS = 1000;
    const FRAME_TOLERANCE_W = 24;
    const FRAME_TOLERANCE_H = 80;

    let modifierActive = false;
    let modifierHeld = false;
    let modifierTimer = null;
    let current = { ...INITIAL };

    const measure = () => ({
        innerWidth: window.innerWidth || 0,
        innerHeight: window.innerHeight || 0,
        outerWidth: window.outerWidth || 0,
        outerHeight: window.outerHeight || 0,
    });

    const isMismatch = () => {
        if (current.mode !== "fixed") return false;
        const m = measure();
        return (
            m.outerWidth > 0 &&
            m.outerHeight > 0 &&
            (Math.abs(m.outerWidth - m.innerWidth) > FRAME_TOLERANCE_W ||
                Math.abs(m.outerHeight - m.innerHeight) > FRAME_TOLERANCE_H)
        );
    };

    const labelFor = () => {
        if (current.mode === "fluid") return "fluid";
        if (isMismatch()) return "fixed mismatch";
        if (current.width && current.height) return `fixed ${current.width}x${current.height}`;
        return "fixed";
    };

    const colorFor = () => {
        if (current.mode === "fluid") return "rgba(22, 101, 52, 0.6)";
        if (isMismatch()) return "rgba(146, 64, 14, 0.72)";
        return "rgba(31, 41, 55, 0.62)";
    };

    const applyInteractive = () => {
        const root = document.getElementById(ROOT_ID);
        if (!root) return;
        root.style.pointerEvents = modifierHeld ? "auto" : "none";
        root.style.cursor = modifierHeld ? "pointer" : "";
        root.style.outline = modifierHeld ? "1px solid rgba(255, 255, 255, 0.28)" : "";
        root.style.outlineOffset = modifierHeld ? "1px" : "";
    };

    const render = () => {
        const root = document.getElementById(ROOT_ID);
        if (!root) return;
        root.textContent = labelFor();
        root.style.background = colorFor();
        root.style.opacity = modifierHeld ? "0.56" : "0.18";
    };

    const closeModal = () => {
        const existing = document.getElementById(MODAL_ID);
        if (existing) existing.remove();
    };

    const setModalMessage = (text, ok) => {
        const msg = document.querySelector(`#${MODAL_ID} [data-role="message"]`);
        if (!msg) return;
        msg.textContent = text;
        msg.style.color = ok ? "rgba(134, 239, 172, 0.95)" : "rgba(252, 165, 165, 0.95)";
    };

    const action = async (name) => {
        if (!window.__octowright_viewport_action) {
            setModalMessage("Viewport action binding is unavailable.", false);
            return;
        }
        try {
            const result = await window.__octowright_viewport_action({ action: name, measured: measure() });
            if (name === "sync") {
                current = { mode: "fixed", width: result.width, height: result.height };
                render();
                setModalMessage(`${result.width}x${result.height}`, true);
            } else if (name === "relaunch-fluid") {
                setModalMessage(result.new_instance_id || "fluid", true);
            }
        } catch (err) {
            setModalMessage(String((err && err.message) || err || "Viewport action failed."), false);
        }
    };

    const makeButton = (label, onClick, primary) => {
        const btn = document.createElement("button");
        btn.textContent = label;
        Object.assign(btn.style, {
            border: "0",
            borderRadius: "5px",
            padding: "5px 9px",
            cursor: "pointer",
            background: primary ? "#2563eb" : "rgba(255, 255, 255, 0.08)",
            color: "white",
            fontWeight: primary ? "600" : "400",
        });
        btn.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            onClick();
        });
        return btn;
    };

    const openModal = () => {
        closeModal();
        const measured = measure();
        const modal = document.createElement("div");
        modal.id = MODAL_ID;
        Object.assign(modal.style, {
            position: "fixed",
            left: "12px",
            bottom: "40px",
            zIndex: "2147483647",
            background: "rgba(15, 23, 42, 0.86)",
            color: "white",
            border: "1px solid rgba(255, 255, 255, 0.1)",
            borderRadius: "6px",
            padding: "8px",
            font: "12px system-ui, sans-serif",
            boxShadow: "0 8px 18px rgba(0, 0, 0, 0.18)",
            minWidth: "0",
        });

        const body = document.createElement("div");
        body.textContent = `${measured.innerWidth}x${measured.innerHeight} / ${measured.outerWidth}x${measured.outerHeight}`;
        Object.assign(body.style, { color: "rgba(255, 255, 255, 0.78)", whiteSpace: "nowrap" });

        const row = document.createElement("div");
        Object.assign(row.style, { display: "flex", gap: "6px", marginTop: "7px", flexWrap: "wrap" });
        row.append(
            makeButton("Sync", () => action("sync"), true),
            makeButton("Fluid", () => action("relaunch-fluid"), false),
        );

        const message = document.createElement("div");
        message.dataset.role = "message";
        Object.assign(message.style, { marginTop: "8px", minHeight: "14px", fontSize: "11px" });

        modal.append(body, row, message);
        document.body.append(modal);
    };

    const build = () => {
        if (!document.body) return null;
        let root = document.getElementById(ROOT_ID);
        if (root) return root;
        root = document.createElement("div");
        root.id = ROOT_ID;
        Object.assign(root.style, {
            position: "fixed",
            left: "12px",
            bottom: "12px",
            zIndex: "2147483646",
            color: "white",
            borderRadius: "5px",
            padding: "2px 5px",
            font: "11px ui-monospace, Menlo, Consolas, monospace",
            boxShadow: "none",
            pointerEvents: "none",
            userSelect: "none",
            transition: "background 160ms ease, opacity 160ms ease",
        });
        root.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            openModal();
        });
        document.body.append(root);
        applyInteractive();
        render();
        return root;
    };

    build();
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", build, { once: true });
    }
    new MutationObserver(() => {
        if (document.body && !document.getElementById(ROOT_ID)) build();
    }).observe(document.documentElement || document, { childList: true, subtree: true });
    window.addEventListener("resize", render);
    window.addEventListener(
        "keydown",
        (event) => {
            if (!event[CLICK_MODIFIER] || modifierActive) return;
            modifierActive = true;
            if (modifierTimer) clearTimeout(modifierTimer);
            modifierTimer = setTimeout(() => {
                modifierHeld = true;
                applyInteractive();
                render();
            }, ALT_HOLD_MS);
            applyInteractive();
            render();
        },
        true,
    );
    window.addEventListener(
        "keyup",
        (event) => {
            if (event[CLICK_MODIFIER]) return;
            modifierActive = false;
            modifierHeld = false;
            if (modifierTimer) {
                clearTimeout(modifierTimer);
                modifierTimer = null;
            }
            closeModal();
            applyInteractive();
            render();
        },
        true,
    );
    window.addEventListener("blur", () => {
        modifierActive = false;
        modifierHeld = false;
        if (modifierTimer) {
            clearTimeout(modifierTimer);
            modifierTimer = null;
        }
        closeModal();
        applyInteractive();
        render();
    });
})();
