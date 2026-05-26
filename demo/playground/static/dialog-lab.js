// Dialog Lab — deterministic native dialog and popup triggers.

(function () {
  const dialogResult = document.getElementById("dialog-result");
  const popupResult = document.getElementById("popup-result");

  async function log(kind, message) {
    await fetch("/api/event", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source: "dialog-lab", kind, message }),
    });
  }

  document.getElementById("alert-button").addEventListener("click", async () => {
    await log("dialog", "alert requested");
    alert("Octowright alert: deterministic message");
    dialogResult.textContent = "Alert closed";
  });

  document.getElementById("confirm-button").addEventListener("click", async () => {
    await log("dialog", "confirm requested");
    const accepted = confirm("Octowright confirm: accept this run?");
    dialogResult.textContent = `Confirm result: ${accepted ? "accepted" : "dismissed"}`;
  });

  document.getElementById("prompt-button").addEventListener("click", async () => {
    await log("dialog", "prompt requested");
    const value = prompt("Octowright prompt: enter a run label", "macro-run");
    dialogResult.textContent = `Prompt result: ${value || "<empty>"}`;
  });

  document.getElementById("popup-button").addEventListener("click", async () => {
    const popup = window.open("/popup-child.html", "octowrightPopup", "width=520,height=420");
    await log("popup", "same-origin popup opened");
    popupResult.textContent = popup ? "Popup opened: /popup-child.html" : "Popup blocked";
  });
})();
