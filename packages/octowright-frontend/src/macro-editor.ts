// Macro editor + repair-preview modals. Both load detail JSON via the
// macro API, render via macro-renderers, and post back through validateMacro
// + updateMacro on save. The dashboard is refreshed via the injected
// refresh callback (supplied by dashboard.ts to avoid a circular import).

import { getMacro, getMacroRepairPreview, updateMacro, validateMacro } from "./api.js";
import {
  renderMacroRepairPreview,
  renderMacroSelectorTools,
  renderMacroSummary,
} from "./macro-renderers.js";
import { showSnackbar } from "./snackbar.js";
import type { SessionListResponse } from "./types.js";

function closeModal(): void {
  document.querySelector(".modal-backdrop")?.remove();
}

export function openMacroEditor(
  name: string,
  sessions: SessionListResponse,
  refreshDashboard: () => void,
): void {
  const existing = document.querySelector(".modal-backdrop");
  if (existing) existing.remove();

  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";

  const modal = document.createElement("div");
  modal.className = "modal modal--macro-editor";
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  modal.setAttribute("aria-label", `Edit macro: ${name}`);

  const header = document.createElement("div");
  header.className = "modal__header";
  const title = document.createElement("h3");
  title.className = "modal__title";
  title.textContent = `Edit macro: ${name}`;
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

  const error = document.createElement("div");
  error.className = "modal__error";
  error.setAttribute("data-testid", `macro-editor-error-${name}`);

  const footer = document.createElement("div");
  footer.className = "modal__footer";

  modal.append(header, body, footer);
  backdrop.append(modal);
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop) closeModal();
  });
  document.body.append(backdrop);

  const setError = (message: string): void => {
    error.textContent = message;
    if (message) {
      error.classList.add("modal__error--visible");
    } else {
      error.classList.remove("modal__error--visible");
    }
  };

  getMacro(name)
    .then((detail) => {
      body.innerHTML = "";
      error.textContent = "";
      error.classList.remove("modal__error--visible");

      const summaryTitle = document.createElement("div");
      summaryTitle.className = "modal__label";
      summaryTitle.textContent = "Macro structure summary";

      const summary = renderMacroSummary(detail);
      const textarea = document.createElement("textarea");
      textarea.className = "yaml-editor";
      textarea.setAttribute("spellcheck", "false");
      textarea.value = JSON.stringify(detail, null, 2);

      const selectorTools = renderMacroSelectorTools(sessions, setError);

      const saveBtn = document.createElement("button");
      saveBtn.className = "btn btn--primary";
      saveBtn.textContent = "Save";
      saveBtn.setAttribute("data-testid", `macro-save-${name}`);

      const cancelBtn = document.createElement("button");
      cancelBtn.className = "btn";
      cancelBtn.textContent = "Cancel";
      cancelBtn.addEventListener("click", closeModal);

      saveBtn.addEventListener("click", () => {
        let macroJson: unknown;
        setError("");
        try {
          macroJson = JSON.parse(textarea.value);
        } catch {
          setError("Macro JSON is invalid.");
          return;
        }

        saveBtn.disabled = true;
        saveBtn.textContent = "Validating…";
        validateMacro(name, macroJson)
          .then((validation) => {
            if (!validation.ok || validation.valid === false) {
              const reasons = validation.issues.map((issue) => `${issue.code}: ${issue.message}`).join("\n");
              throw new Error(reasons || "Macro validation failed.");
            }
            return updateMacro(name, macroJson);
          })
          .then(() => {
            showSnackbar(`Macro "${name}" updated.`);
            closeModal();
            refreshDashboard();
          })
          .catch((err: unknown) => {
            setError(`Save failed: ${String(err)}`);
            saveBtn.disabled = false;
            saveBtn.textContent = "Save";
          });
      });

      footer.innerHTML = "";
      footer.append(cancelBtn, saveBtn);
      body.append(summaryTitle, summary, textarea, selectorTools, error);
    })
    .catch((err: unknown) => {
      loadingMsg.textContent = `Failed to load macro: ${String(err)}`;
    });
}

export function openMacroRepairPreview(name: string): void {
  const existing = document.querySelector(".modal-backdrop");
  if (existing) existing.remove();

  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";

  const modal = document.createElement("div");
  modal.className = "modal";
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  modal.setAttribute("aria-label", `Repair preview: ${name}`);

  const header = document.createElement("div");
  header.className = "modal__header";
  const title = document.createElement("h3");
  title.className = "modal__title";
  title.textContent = `Repair preview: ${name}`;
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
  const doneBtn = document.createElement("button");
  doneBtn.className = "btn";
  doneBtn.textContent = "Close";
  doneBtn.addEventListener("click", closeModal);
  footer.append(doneBtn);

  modal.append(header, body, footer);
  backdrop.append(modal);
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop) closeModal();
  });
  document.body.append(backdrop);

  getMacroRepairPreview(name)
    .then((preview) => {
      body.innerHTML = "";
      body.append(renderMacroRepairPreview(preview));
    })
    .catch((err: unknown) => {
      loadingMsg.textContent = `Failed to load repair preview: ${String(err)}`;
    });
}
