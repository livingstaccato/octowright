// Frame Lab parent — records child-frame postMessage events.

(function () {
  const result = document.getElementById("frame-result");

  window.addEventListener("message", async (event) => {
    // Reject cross-origin posts before doing anything with the payload —
    // any tab on the network can postMessage into our window, and the
    // source-tag check alone is spoof-able.
    if (event.origin !== window.location.origin) return;
    if (!event.data || event.data.source !== "octowright-frame-child") return;
    result.textContent = `Child frame message: ${event.data.value}`;
    await fetch("/api/event", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        source: "frame-lab",
        kind: "frame",
        message: `child submitted ${event.data.value}`,
      }),
    });
  });
})();
