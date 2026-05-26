// Storage Console — deterministic local/session/cookie state exercise.

(function () {
  const key = document.getElementById("storage-key");
  const value = document.getElementById("storage-value");
  const result = document.getElementById("storage-result");

  async function log(kind, message) {
    await fetch("/api/event", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source: "storage-console", kind, message }),
    });
  }

  function getCookie(name) {
    // Filter document.cookie down to just the requested key. localhost shares
    // cookies across all ports, so dumping the whole jar leaks unrelated test
    // state and makes integration assertions flaky.
    const prefix = encodeURIComponent(name) + "=";
    for (const raw of document.cookie.split(";")) {
      const c = raw.trim();
      if (c.startsWith(prefix)) {
        return decodeURIComponent(c.slice(prefix.length));
      }
    }
    return null;
  }

  function render() {
    const currentKey = key.value;
    result.textContent = JSON.stringify({
      localStorage: localStorage.getItem(currentKey),
      sessionStorage: sessionStorage.getItem(currentKey),
      cookie: getCookie(currentKey),
    }, null, 2);
  }

  document.getElementById("write-storage").addEventListener("click", async () => {
    localStorage.setItem(key.value, value.value);
    sessionStorage.setItem(key.value, value.value);
    document.cookie = `${encodeURIComponent(key.value)}=${encodeURIComponent(value.value)}; path=/; SameSite=Lax`;
    render();
    await log("storage", `wrote ${key.value}`);
  });

  document.getElementById("clear-storage").addEventListener("click", async () => {
    localStorage.removeItem(key.value);
    sessionStorage.removeItem(key.value);
    document.cookie = `${encodeURIComponent(key.value)}=; Max-Age=0; path=/`;
    render();
    await log("storage", `cleared ${key.value}`);
  });

  render();
})();
