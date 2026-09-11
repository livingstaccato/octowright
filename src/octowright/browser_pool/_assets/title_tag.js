(() => {
    // A subframe has no window title of its own, so tagging there can only ever
    // be wasted work -- and on a page that spawns frames it multiplies the
    // prototype patch and the observer below once per frame. The other four
    // injected assets all carry this guard; this one was the exception.
    if (window.top !== window.self) return;

    const SUFFIX = __SUFFIX__;
    // SUFFIX is " (emoji) [tag]" with a leading space. Browsers strip trailing
    // whitespace from titles on read, but a leading space inside the actual
    // value survives. We compare on a trimmed anchor so any double-injection
    // (e.g. "Yahoo (🐬🦊) [acct] (🐬🦊) [acct]") collapses back to a single tag.
    const SUFFIX_BASE = SUFFIX.replace(/^\s+/, "");

    const desc = Object.getOwnPropertyDescriptor(Document.prototype, "title");
    const tagged = (s) => s.endsWith(SUFFIX_BASE);
    const untag = (s) => {
        if (s.endsWith(SUFFIX)) return s.slice(0, s.length - SUFFIX.length);
        if (s.endsWith(SUFFIX_BASE)) return s.slice(0, s.length - SUFFIX_BASE.length);
        return s;
    };

    // WHY THE GETTER IS PATCHED, NOT JUST THE SETTER.
    //
    // A page that re-asserts its own title used to fight this script: we append
    // the suffix, the page sees a title it did not write, restores its value,
    // we append again. Both sides' writes go through the setter, so the
    // `cur !== want` guard could never break it -- that only stops us
    // re-entering ourselves -- and unbounded it pegs the renderer's main
    // thread. Measured 2026-09-11 on a bot-challenge page (which rewrites its
    // title to show status): a Firefox content process allocated ~1GB/s,
    // reached 20GB in 140s, and OOM-killed every sibling browser in the pool.
    // Raw Playwright on the same page with no init scripts stayed flat at
    // 0.54GB, so the page was never the cause.
    //
    // Backing off on a timer was tried first and is NOT enough: the tag is
    // restored, the page reverts it within a microtask, and the window title a
    // human is meant to read never actually shows it. The fight has to not
    // start. So the page is handed back its OWN value on read -- the tag is
    // invisible to page code -- while the real <title> node, which is what the
    // window and tab actually render, keeps it. A page comparing
    // `document.title` against what it last set now finds them equal and never
    // reverts anything.
    //
    // Masking costs octowright's own tooling nothing, which is not obvious and
    // is measured rather than assumed: Playwright evaluates `document.title` in
    // an ISOLATED world, which has its own `Document.prototype`, so this patch
    // -- installed in the main world -- is not there and `page.title()` reads
    // the real tagged value. Verified end to end (`page.title()` and the
    // <title> node both returned "Hello (emoji) [tagcheck]"). So the tag is
    // visible everywhere it is meant to be -- window chrome, tab, browser_list,
    // page outlines -- and invisible only to the page's own scripts, which are
    // the one reader it must not provoke.
    let pageValue = null;

    const realGet = () => {
        try {
            return desc.get.call(document) || "";
        } catch (_) {
            return "";
        }
    };

    if (desc && desc.get && desc.set) {
        Object.defineProperty(Document.prototype, "title", {
            configurable: true,
            enumerable: desc.enumerable,
            get() {
                const real = desc.get.call(this);
                // Only mask the exact value we produced from the page's own
                // last write. Anything else (a title set through the <title>
                // node directly, a value from before this ran) is returned
                // verbatim rather than guessed at.
                if (pageValue !== null && real === pageValue + SUFFIX) return pageValue;
                return real;
            },
            set(v) {
                const s = String(v == null ? "" : v);
                pageValue = untag(s);
                desc.set.call(this, tagged(s) ? s : s + SUFFIX);
            },
        });
    }

    // Backstop. The masking above removes the cause, but it can only cover
    // reads that go through `document.title`; a page could still drive a loop
    // some other way (writing the <title> node's text directly, say). A wedged
    // renderer is severe enough -- it took out unrelated browsers and the
    // daemon's own sessions -- that it gets a second, independent bound: past a
    // burst, stop re-applying inline and let the page's value stand. Two title
    // writes a second is nothing; the original was thousands.
    const BURST_LIMIT = 20;
    const BURST_WINDOW_MS = 1000;
    let windowStart = 0;
    let windowCount = 0;
    let yielded = false;

    const burstExceeded = () => {
        const t = Date.now();
        if (t - windowStart > BURST_WINDOW_MS) {
            windowStart = t;
            windowCount = 0;
            yielded = false;
        }
        windowCount += 1;
        return windowCount > BURST_LIMIT;
    };

    const apply = () => {
        try {
            // Read the REAL title, never the masked getter -- otherwise this
            // reads back the page's value, concludes the tag is missing, and
            // rewrites it on every single mutation.
            const real = realGet();
            if (tagged(real)) return;
            if (yielded) return;
            if (burstExceeded()) {
                yielded = true;
                return;
            }
            pageValue = untag(real);
            desc.set.call(document, pageValue + SUFFIX);
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
