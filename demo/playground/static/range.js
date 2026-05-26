// Test Range home — mirrors shared server state and recent log events.

(function () {
  const eventLog = document.getElementById("event-log");
  const tiles = document.getElementById("state-tiles");
  const forms = document.getElementById("state-forms");
  const events = document.getElementById("state-events");
  const downloads = document.getElementById("state-downloads");
  const reset = document.getElementById("reset-range");

  function claimedTileCount(canvas) {
    return (canvas || []).flat().filter(Boolean).length;
  }

  function renderCounters(state) {
    tiles.textContent = String(claimedTileCount(state.canvas));
    forms.textContent = String((state.form_steps || []).length);
    events.textContent = String((state.events || []).length);
    downloads.textContent = String(state.downloads_served || 0);
  }

  function appendEvent(entry) {
    const empty = eventLog.querySelector(".empty");
    if (empty) empty.remove();
    const li = document.createElement("li");
    const time = document.createElement("time");
    const label = document.createElement("span");
    time.textContent = new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    label.textContent = `${entry.kind || "event"}: ${entry.message || "updated"}`;
    li.append(time, label);
    eventLog.prepend(li);
    while (eventLog.children.length > 8) {
      eventLog.lastElementChild.remove();
    }
  }

  function applySnapshot(state) {
    renderCounters(state);
    eventLog.innerHTML = "";
    const entries = state.events || [];
    if (entries.length === 0) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "Waiting for activity...";
      eventLog.append(li);
      return;
    }
    for (const entry of entries.slice(-8)) {
      appendEvent(entry);
    }
  }

  reset.addEventListener("click", async () => {
    await fetch("/api/reset", { method: "POST" });
  });

  const es = new EventSource("/api/events");
  es.onmessage = (msg) => {
    try {
      const event = JSON.parse(msg.data);
      if (event.event === "snapshot") {
        applySnapshot(event);
      } else if (event.event === "tile_claimed") {
        fetch("/api/state").then((res) => res.json()).then(renderCounters);
        appendEvent({ kind: "canvas", message: `${event.claimed_by} claimed ${event.row},${event.col}` });
      } else if (event.event === "form_step") {
        fetch("/api/state").then((res) => res.json()).then(renderCounters);
        appendEvent({ kind: "form", message: `step ${event.step}: ${event.label}` });
      } else if (event.event === "log_event") {
        fetch("/api/state").then((res) => res.json()).then(renderCounters);
        appendEvent(event);
      } else if (event.event === "reset") {
        applySnapshot({ canvas: [], form_steps: [], events: [], downloads_served: 0 });
      }
    } catch {
      /* ignore malformed frames */
    }
  };
})();
