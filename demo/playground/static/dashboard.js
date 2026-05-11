// Dashboard page — subscribes to SSE and renders each form-step event as a
// table row. Pure DOM construction; no innerHTML with server data.

(function () {
  const rows = document.getElementById("rows");
  let row_count = 0;

  function renderEmptyIfNeeded() {
    if (row_count !== 0) return;
    rows.innerHTML = "";
    const tr = document.createElement("tr");
    tr.className = "empty";
    const td = document.createElement("td");
    td.colSpan = 4;
    td.textContent = "Waiting for events…";
    tr.append(td);
    rows.append(tr);
  }

  function clearEmpty() {
    const empty = rows.querySelector("tr.empty");
    if (empty) empty.remove();
  }

  function appendRow(step, label, value) {
    clearEmpty();
    row_count += 1;
    const tr = document.createElement("tr");
    tr.setAttribute("data-testid", `event-row-${row_count}`);
    for (const text of [String(row_count), String(step), label, value]) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.append(td);
    }
    rows.append(tr);
  }

  function reset() {
    row_count = 0;
    rows.innerHTML = "";
    renderEmptyIfNeeded();
  }

  function applySnapshot(form_steps) {
    rows.innerHTML = "";
    row_count = 0;
    if (!form_steps || form_steps.length === 0) {
      renderEmptyIfNeeded();
      return;
    }
    for (const s of form_steps) {
      appendRow(s.step, s.label, s.value);
    }
  }

  const es = new EventSource("/api/events");
  es.onmessage = (msg) => {
    try {
      const event = JSON.parse(msg.data);
      if (event.event === "snapshot") {
        applySnapshot(event.form_steps);
      } else if (event.event === "form_step") {
        appendRow(event.step, event.label, event.value);
      } else if (event.event === "reset") {
        reset();
      }
    } catch {
      /* ignore */
    }
  };

  renderEmptyIfNeeded();
})();
