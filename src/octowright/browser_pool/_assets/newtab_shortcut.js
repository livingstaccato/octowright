(() => {
    // WebKit-only Cmd+T / Ctrl+T handler.
    //
    // Playwright's WebKit is a minimal shell with no browser chrome, so it
    // doesn't bind Cmd+T to "new tab" — pressing it normally does nothing.
    // Because the shell doesn't intercept the shortcut, the keydown reaches
    // the page, letting us open octowright's /new-tab via window.open (a valid
    // user-gesture navigation, so it isn't popup-blocked). Injected for WebKit
    // only; Chromium/Firefox handle Cmd+T at the browser-chrome level and would
    // double-open if this ran there.
    if (window.top !== window.self) return;
    const TARGET = __TARGET__;
    document.addEventListener(
        "keydown",
        (e) => {
            const isNewTab = (e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.code === "KeyT";
            if (!isNewTab) return;
            e.preventDefault();
            window.open(TARGET, "_blank");
        },
        true,
    );
})();
