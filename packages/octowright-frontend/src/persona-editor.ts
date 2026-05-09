// Persona YAML editor modal: loads the persona detail, lets the user edit
// the YAML inline, and posts back via updatePersonaYaml. On save the
// dashboard is refreshed via the supplied refresh callback.

import { getPersonaDetail, updatePersonaYaml } from "./api.js";
import { formatBytes } from "./format.js";
import { showSnackbar } from "./snackbar.js";

function closeModal(): void {
  document.querySelector(".modal-backdrop")?.remove();
}

function diskEntry(label: string, value: string): HTMLElement {
  const el = document.createElement("div");
  el.className = "disk-entry";
  const lbl = document.createElement("span");
  lbl.className = "disk-entry__label";
  lbl.textContent = label;
  const val = document.createElement("span");
  val.className = "disk-entry__value";
  val.textContent = value;
  el.append(lbl, val);
  return el;
}

export function openPersonaEditor(name: string): void {
  const existing = document.querySelector(".modal-backdrop");
  if (existing) existing.remove();

  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";

  const modal = document.createElement("div");
  modal.className = "modal";
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  modal.setAttribute("aria-label", `Edit persona: ${name}`);

  const header = document.createElement("div");
  header.className = "modal__header";
  const title = document.createElement("h3");
  title.className = "modal__title";
  title.textContent = `Edit persona: ${name}`;
  const closeBtn = document.createElement("button");
  closeBtn.className = "icon-btn";
  closeBtn.setAttribute("aria-label", "Close");
  closeBtn.textContent = "✕";
  closeBtn.addEventListener("click", closeModal);
  header.append(title, closeBtn);

  const body = document.createElement("div");
  body.className = "modal__body";

  const loadingMsg = document.createElement("p");
  loadingMsg.className = "modal__loading";
  loadingMsg.textContent = "Loading…";
  body.append(loadingMsg);

  const footer = document.createElement("div");
  footer.className = "modal__footer";

  modal.append(header, body, footer);
  backdrop.append(modal);
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop) closeModal();
  });
  document.body.append(backdrop);

  getPersonaDetail(name)
    .then((detail) => {
      body.innerHTML = "";

      const diskInfo = document.createElement("div");
      diskInfo.className = "modal__disk-info";
      diskInfo.append(diskEntry("Total on disk", formatBytes(detail.disk_bytes)));
      for (const [engine, bytes] of Object.entries(detail.engine_bytes)) {
        diskInfo.append(diskEntry(engine, formatBytes(bytes)));
      }
      body.append(diskInfo);

      const textarea = document.createElement("textarea");
      textarea.className = "yaml-editor";
      textarea.setAttribute("spellcheck", "false");
      textarea.value = detail.yaml;
      body.append(textarea);

      const saveBtn = document.createElement("button");
      saveBtn.className = "btn btn--primary";
      saveBtn.textContent = "Save";

      const cancelBtn = document.createElement("button");
      cancelBtn.className = "btn";
      cancelBtn.textContent = "Cancel";
      cancelBtn.addEventListener("click", closeModal);

      saveBtn.addEventListener("click", () => {
        saveBtn.disabled = true;
        saveBtn.textContent = "Saving…";
        updatePersonaYaml(name, textarea.value)
          .then(() => {
            showSnackbar(`Persona "${name}" saved.`);
            closeModal();
          })
          .catch((err: unknown) => {
            showSnackbar(`Save failed: ${String(err)}`, true);
            saveBtn.disabled = false;
            saveBtn.textContent = "Save";
          });
      });

      footer.innerHTML = "";
      footer.append(cancelBtn, saveBtn);
    })
    .catch((err: unknown) => {
      loadingMsg.textContent = `Failed to load persona: ${String(err)}`;
    });
}
