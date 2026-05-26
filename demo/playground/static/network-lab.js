// Network Lab — deterministic fetches for request/response inspection.

(function () {
  const result = document.getElementById("network-result");

  async function log(kind, message) {
    await fetch("/api/event", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source: "network-lab", kind, message }),
    });
  }

  async function showResponse(label, fetcher) {
    const started = performance.now();
    try {
      const res = await fetcher();
      const text = await res.text();
      const elapsed = Math.round(performance.now() - started);
      result.textContent = `${label}\nstatus: ${res.status}\nelapsed_ms: ${elapsed}\n${text}`;
      await log("network", `${label} ${res.status}`);
    } catch (error) {
      result.textContent = `${label}\nfailed: ${error}`;
      await log("network", `${label} failed`);
    }
  }

  document.getElementById("ping-request").addEventListener("click", () => {
    showResponse("GET /api/ping", () => fetch("/api/ping"));
  });

  document.getElementById("echo-request").addEventListener("click", () => {
    showResponse("POST /api/echo", () => fetch("/api/echo", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ value: "octowright", mode: "echo" }),
    }));
  });

  document.getElementById("delay-request").addEventListener("click", () => {
    showResponse("GET /api/delay?ms=300", () => fetch("/api/delay?ms=300"));
  });

  document.getElementById("error-request").addEventListener("click", () => {
    showResponse("GET /api/error", () => fetch("/api/error"));
  });
})();
