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
