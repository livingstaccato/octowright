(() => {
    const SUFFIX = __SUFFIX__;
    // SUFFIX is " (emoji) [tag]" with a leading space. Browsers strip trailing
    // whitespace from titles on read, but a leading space inside the actual
    // value survives. We compare on a trimmed anchor so any double-injection
    // (e.g. "Yahoo (🐬🦊) [acct] (🐬🦊) [acct]") collapses back to a single tag.
    const SUFFIX_BASE = SUFFIX.replace(/^\s+/, "");
    const ensure = (v) => {
        const s = String(v == null ? "" : v);
        return s.endsWith(SUFFIX_BASE) ? s : s + SUFFIX;
    };
    const desc = Object.getOwnPropertyDescriptor(Document.prototype, "title");
    if (desc && desc.get && desc.set) {
        Object.defineProperty(Document.prototype, "title", {
            configurable: true,
            enumerable: desc.enumerable,
            get() { return desc.get.call(this); },
            set(v) { desc.set.call(this, ensure(v)); },
        });
    }
    const apply = () => {
        try {
            const cur = document.title || "";
            const want = ensure(cur);
            if (cur !== want) document.title = want;
        } catch (_) {}
    };
    apply();
    const watchHead = () => {
        const head = document.querySelector("head");
        if (!head) return false;
        new MutationObserver(apply).observe(head, {
            subtree: true, childList: true, characterData: true,
        });
        return true;
    };
    const onReady = () => { watchHead(); apply(); };
    if (!watchHead()) {
        document.addEventListener("DOMContentLoaded", onReady, { once: true });
    }
    window.addEventListener("load", apply, { once: true });
})();
