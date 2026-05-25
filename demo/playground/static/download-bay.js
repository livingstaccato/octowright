// Download Bay — records upload input changes and download intent.

(function () {
  const upload = document.getElementById("upload-file");
  const result = document.getElementById("upload-result");
  const download = document.getElementById("download-report");

  async function log(kind, message) {
    await fetch("/api/event", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source: "download-bay", kind, message }),
    });
  }

  download.addEventListener("click", () => {
    log("download", "download link clicked");
  });

  upload.addEventListener("change", async () => {
    const file = upload.files && upload.files[0];
    if (!file) {
      result.textContent = "No file selected.";
      return;
    }
    result.textContent = `Selected: ${file.name}\nsize: ${file.size} bytes\ntype: ${file.type || "unknown"}`;
    await log("upload", `selected ${file.name}`);
  });
})();
