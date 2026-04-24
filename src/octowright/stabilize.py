from __future__ import annotations

STABILIZE_SCRIPT = r'''
(() => {
    // 1. Freeze Date.now + Date constructor to a fixed epoch
    const FROZEN_EPOCH = 1700000000000; // 2023-11-14T22:13:20Z — deterministic
    const RealDate = Date;
    const frozenNow = () => FROZEN_EPOCH;
    try {
        Date.now = frozenNow;
        performance.now = () => 0;
    } catch (_) {}

    // 2. Tame requestAnimationFrame — fire synchronously
    try {
        window.requestAnimationFrame = (cb) => { cb(frozenNow()); return 0; };
        window.cancelAnimationFrame = () => {};
    } catch (_) {}

    // 3. Kill CSS transitions + animations globally
    const style = document.createElement("style");
    style.textContent = `
        *, *::before, *::after {
            animation-duration: 0ms !important;
            animation-delay: 0ms !important;
            transition-duration: 0ms !important;
            transition-delay: 0ms !important;
            scroll-behavior: auto !important;
        }
    `;
    const install = () => {
        (document.head || document.documentElement).appendChild(style);
    };
    if (document.head) install();
    else document.addEventListener("DOMContentLoaded", install, { once: true });
})();
'''


def render_stabilize_script() -> str:
    """Return the stabilize init script verbatim. Parameterisation can come later."""
    return STABILIZE_SCRIPT
