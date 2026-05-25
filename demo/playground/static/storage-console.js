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

  function render() {
    const currentKey = key.value;
    result.textContent = JSON.stringify({
      localStorage: localStorage.getItem(currentKey),
      sessionStorage: sessionStorage.getItem(currentKey),
      cookie: document.cookie || "",
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
