// Frame Lab parent — records child-frame postMessage events.

(function () {
  const result = document.getElementById("frame-result");

  window.addEventListener("message", async (event) => {
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
