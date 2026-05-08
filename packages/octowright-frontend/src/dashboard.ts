import {
  dashboardEventsUrl,
  deleteRecording,
  getMacro,
  getMacroRepairPreview,
  validateMacro,
  validateSessionSelector,
  getMacros,
  getPersonaDetail,
  getPersonas,
  getPersonaSizes,
  getScenarios,
  getSessions,
  updateMacro,
  relaunchSession,
  startScenario,
  updatePersonaYaml,
} from "./api.js";
import { formatDateTime, shortUrl } from "./format.js";
import { getLogger, initTelemetry } from "./telemetry.js";
import type {
  LiveScenario,
  MacroAction,
  MacroDetail,
  MacroRepairPreview,
  MacroSummary,
  PersonaSummary,
  SavedScenario,
  ScenarioListResponse,
  SessionListResponse,
  SessionSummary,
} from "./types.js";

const REFRESH_MS = 5000;

const log = getLogger("octowright.frontend.dashboard");

export type DashboardDisposer = () => void;

interface DashboardState {
  sessions: SessionListResponse;
  scenarios: ScenarioListResponse;
  personas: PersonaSummary[];
  macros: MacroSummary[];
}

type DashboardScope = "sessions" | "scenarios" | "personas" | "macros";

const EMPTY_STATE: DashboardState = {
  sessions: { live: [], closed: [] },
  scenarios: { live: [] },
  personas: [],
  macros: [],
};

// Module-level persona sizes cache — populated lazily so initial render isn't blocked.
let personaSizes: Record<string, number | null> = {};
let sizesLoaded = false;

function loadPersonaSizes(): void {
  getPersonaSizes()
    .then((sizes) => {
      personaSizes = sizes;
      sizesLoaded = true;
    })
    .catch(() => {
      sizesLoaded = true;
    });
}

// ─── snackbar ────────────────────────────────────────────────────────────────

let snackbarEl: HTMLElement | null = null;
let snackbarTimer: ReturnType<typeof setTimeout> | null = null;

function getSnackbar(): HTMLElement {
  if (!snackbarEl) {
    snackbarEl = document.createElement("div");
    snackbarEl.className = "snackbar snackbar--hidden";
    document.body.append(snackbarEl);
  }
  return snackbarEl;
}

export function showSnackbar(msg: string, isError = false): void {
  const el = getSnackbar();
  el.textContent = msg;
  el.className = `snackbar${isError ? " snackbar--error" : ""}`;
  if (snackbarTimer !== null) clearTimeout(snackbarTimer);
  snackbarTimer = setTimeout(() => {
    el.className = "snackbar snackbar--hidden";
    snackbarTimer = null;
  }, 3500);
}

// ─── modal ───────────────────────────────────────────────────────────────────

function closeModal(): void {
  document.querySelector(".modal-backdrop")?.remove();
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

function openMacroEditor(name: string, sessions: SessionListResponse): void {
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

  const refreshDashboard = (): void => {
    if (dashboardRoot) {
      const root = dashboardRoot;
      loadState()
        .then((state) => {
          renderDashboard(root, state);
        })
        .catch(() => {
          // Ignore refresh errors here; the editor is already closed.
        });
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
            if (!validation.ok || !validation.valid) {
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

function renderMacroSummary(detail: MacroDetail): HTMLElement {
  const container = document.createElement("div");
  container.className = "macro-summary";
  const actions = Array.isArray(detail.actions) ? detail.actions : [];
  const count = document.createElement("div");
  count.className = "macro-summary__count";
  count.textContent = `${actions.length} action(s)`;
  const summaryList = renderMacroActionSummaryList(actions);
  container.append(count, summaryList);
  return container;
}

function renderMacroActionSummaryList(actions: MacroAction[]): HTMLElement {
  const ul = document.createElement("ul");
  ul.className = "macro-summary__list";
  if (!Array.isArray(actions) || actions.length === 0) {
    const empty = document.createElement("li");
    empty.className = "macro-summary__item macro-summary__item--empty";
    empty.textContent = "No actions";
    ul.append(empty);
    return ul;
  }
  for (const action of actions) {
    ul.append(renderMacroActionSummaryItem(action));
  }
  return ul;
}

function renderMacroActionSummaryItem(action: MacroAction): HTMLLIElement {
  const li = document.createElement("li");
  li.className = "macro-summary__item";
  const title = document.createElement("div");
  title.className = "macro-summary__action";

  if (action.action === "if_selector") {
    title.textContent = `if_selector ${String(action.selector ?? "∅")}`;
    const meta = document.createElement("span");
    meta.className = "macro-summary__meta";
    const thenCount = Array.isArray(action.then) ? action.then.length : 0;
    const elseCount = Array.isArray(action.else) ? action.else.length : 0;
    meta.textContent = `${thenCount} then · ${elseCount} else`;
    title.append(" ");
    title.append(meta);
    li.append(title);

    const thenWrap = document.createElement("ul");
    thenWrap.className = "macro-summary__branch";
    thenWrap.setAttribute("data-branch", "then");
    thenWrap.append(renderMacroActionSummaryList(action.then ?? []));
    const elseWrap = document.createElement("ul");
    elseWrap.className = "macro-summary__branch";
    elseWrap.setAttribute("data-branch", "else");
    elseWrap.append(renderMacroActionSummaryList(action.else ?? []));
    li.append(thenWrap, elseWrap);
    return li;
  }

  if (action.action === "try_each") {
    const branches = Array.isArray(action.branches) ? action.branches : [];
    title.textContent = `try_each (${branches.length} branch${branches.length === 1 ? "" : "es"})`;
    li.append(title);
    for (let i = 0; i < branches.length; i++) {
      const branchWrap = document.createElement("div");
      branchWrap.className = "macro-summary__branch";
      const branchLabel = document.createElement("div");
      branchLabel.className = "macro-summary__meta";
      const branch = branches[i] ?? [];
      branchLabel.textContent = `branch ${i + 1} (${branch.length} action${branch.length === 1 ? "" : "s"})`;
      branchWrap.append(branchLabel);
      branchWrap.append(renderMacroActionSummaryList(branch));
      li.append(branchWrap);
    }
    return li;
  }

  title.textContent = action.action || "(unknown action)";
  if (typeof action.selector === "string") {
    const meta = document.createElement("span");
    meta.className = "macro-summary__meta";
    meta.textContent = action.selector;
    title.append(" ");
    title.append(meta);
  }
  li.append(title);
  return li;
}

function renderMacroSelectorTools(
  sessions: SessionListResponse,
  setError: (message: string) => void,
): HTMLElement {
  const wrappers = document.createElement("div");
  wrappers.className = "macro-selector-tools";
  const sectionTitle = document.createElement("div");
  sectionTitle.className = "modal__label";
  sectionTitle.textContent = "Live selector check";

  const allSessions = [...sessions.live, ...sessions.closed];
  if (allSessions.length === 0) {
    const empty = document.createElement("p");
    empty.className = "macro-selector-tools__empty";
    empty.textContent = "No sessions available for selector check.";
    wrappers.append(sectionTitle, empty);
    return wrappers;
  }

  const controls = document.createElement("div");
  controls.className = "macro-selector-tools__controls";

  const sessionSelect = document.createElement("select");
  sessionSelect.className = "macro-selector-tools__session";
  for (const session of allSessions) {
    const option = document.createElement("option");
    option.value = session.id;
    option.textContent = session.live ? `${session.id} (live)` : `${session.id} (closed)`;
    sessionSelect.append(option);
  }

  const selectorInput = document.createElement("input");
  selectorInput.className = "macro-selector-tools__selector";
  selectorInput.type = "text";
  selectorInput.placeholder = "#submit";

  const validateBtn = document.createElement("button");
  validateBtn.className = "btn";
  validateBtn.textContent = "Validate selector";

  const validateStatus = document.createElement("div");
  validateStatus.className = "macro-selector-tools__status";

  validateBtn.addEventListener("click", () => {
    const selector = selectorInput.value.trim();
    const sessionId = sessionSelect.value;
    if (!selector) {
      setError("Enter a selector to validate.");
      return;
    }
    setError("");
    validateBtn.disabled = true;
    validateBtn.textContent = "Checking…";
    validateStatus.classList.remove("macro-selector-tools__status--error");
    validateSessionSelector(sessionId, selector)
      .then((result) => {
        validateStatus.textContent = `Selector ${result.present ? "found" : "not found"} in session ${sessionId}.`;
      })
      .catch((err: unknown) => {
        validateStatus.textContent = `Validation failed: ${String(err)}`;
        validateStatus.classList.add("macro-selector-tools__status--error");
      })
      .finally(() => {
        validateBtn.disabled = false;
        validateBtn.textContent = "Validate selector";
      });
  });

  controls.append(sessionSelect, selectorInput, validateBtn);
  wrappers.append(sectionTitle, controls, validateStatus);
  return wrappers;
}

function renderMacroRepairPreview(preview: MacroRepairPreview): HTMLElement {
  if (preview.suggestions.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No selector-based repair suggestions.";
    return empty;
  }

  const list = document.createElement("ol");
  list.className = "repair-preview";
  for (const suggestion of preview.suggestions) {
    const item = document.createElement("li");
    item.className = "repair-preview__item";

    const title = document.createElement("div");
    title.className = "repair-preview__title";
    title.textContent = `Action ${suggestion.action_index}`;

    const prompt = document.createElement("p");
    prompt.className = "repair-preview__prompt";
    prompt.textContent = suggestion.prompt;

    item.append(title);
    if (suggestion.action_preview) {
      const previewText = document.createElement("div");
      previewText.className = "repair-preview__action";
      previewText.textContent = suggestion.action_preview;
      item.append(previewText);
    }

    if (suggestion.replacement_action) {
      const code = document.createElement("pre");
      code.className = "repair-preview__json";
      code.textContent = JSON.stringify(suggestion.replacement_action, null, 2);
      item.append(code);
    }
    item.append(prompt);
    list.append(item);
  }
  return list;
}

// ─── state loading ────────────────────────────────────────────────────────────

export async function loadState(): Promise<DashboardState> {
  const [sessions, scenarios, personas, macros] = await Promise.all([
    getSessions().catch(() => EMPTY_STATE.sessions),
    getScenarios().catch(() => EMPTY_STATE.scenarios),
    getPersonas().catch<PersonaSummary[]>(() => []),
    getMacros().catch<MacroSummary[]>(() => []),
  ]);
  return { sessions, scenarios, personas, macros };
}

async function refreshScopedState(
  current: DashboardState,
  scopes: ReadonlySet<DashboardScope>,
): Promise<DashboardState> {
  const next: DashboardState = { ...current };
  const jobs: Promise<void>[] = [];
  if (scopes.has("sessions")) {
    jobs.push(
      getSessions()
        .then((sessions) => {
          next.sessions = sessions;
        })
        .catch(() => {
          next.sessions = EMPTY_STATE.sessions;
        }),
    );
  }
  if (scopes.has("scenarios")) {
    jobs.push(
      getScenarios()
        .then((scenarios) => {
          next.scenarios = scenarios;
        })
        .catch(() => {
          next.scenarios = EMPTY_STATE.scenarios;
        }),
    );
  }
  if (scopes.has("personas")) {
    jobs.push(
      getPersonas()
        .then((personas) => {
          next.personas = personas;
        })
        .catch(() => {
          next.personas = [];
        }),
    );
  }
  if (scopes.has("macros")) {
    jobs.push(
      getMacros()
        .then((macros) => {
          next.macros = macros;
        })
        .catch(() => {
          next.macros = [];
        }),
    );
  }
  await Promise.all(jobs);
  return next;
}

function parseInvalidateScopes(data: string | null | undefined): ReadonlySet<DashboardScope> | null {
  if (!data) return null;
  try {
    const parsed = JSON.parse(data) as { scope?: unknown };
    const raw = parsed.scope;
    if (typeof raw === "string") {
      const parts = raw
        .split(",")
        .map((piece) => piece.trim())
        .filter(Boolean);
      const scopes = new Set<DashboardScope>();
      for (const part of parts) {
        if (part === "sessions" || part === "scenarios" || part === "personas" || part === "macros") {
          scopes.add(part);
        }
      }
      return scopes.size > 0 ? scopes : null;
    }
  } catch {
    return null;
  }
  return null;
}

// ─── render ───────────────────────────────────────────────────────────────────

export function renderDashboard(root: HTMLElement, state: DashboardState): void {
  const openPanels = new Map(
    Array.from(root.querySelectorAll<HTMLDetailsElement>("details[data-testid]")).map((el) => [
      el.dataset.testid ?? "",
      el.open,
    ]),
  );
  root.innerHTML = "";
  root.append(
    section("Live browsers", "live-browsers", renderSessionTable(state.sessions.live, true)),
    section("Live scenarios", "live-scenarios", renderScenarioList(state.scenarios.live)),
    section("Personas", "personas", renderPersonaGrid(state.personas)),
    section(
      "Saved scenarios",
      "saved-scenarios",
      renderSavedScenarios(state.scenarios.saved ?? []),
    ),
    section(
      "Recent closed sessions",
      "closed-sessions",
      renderSessionTable(state.sessions.closed.slice(0, 20), false),
    ),
    section("Macros", "macros", renderMacroList(state), {
      collapsible: true,
      open: openPanels.get("panel-macros") ?? false,
    }),
  );
}

function section(
  title: string,
  testid: string,
  body: HTMLElement,
  opts: { collapsible?: boolean; open?: boolean } = {},
): HTMLElement {
  const wrapper = opts.collapsible ? document.createElement("details") : document.createElement("section");
  wrapper.className = `panel panel--${testid}`;
  wrapper.setAttribute("data-testid", `panel-${testid}`);
  if (opts.collapsible && wrapper instanceof HTMLDetailsElement) {
    wrapper.open = opts.open ?? false;
  }
  const heading = opts.collapsible ? document.createElement("summary") : document.createElement("h2");
  heading.className = "panel__title";
  heading.textContent = title;
  wrapper.append(heading, body);
  return wrapper;
}

function renderSessionTable(rows: SessionSummary[], live: boolean): HTMLElement {
  if (rows.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = live ? "No live browsers." : "No closed sessions yet.";
    return empty;
  }
  const table = document.createElement("table");
  table.className = "data-table";
  table.setAttribute("data-testid", live ? "table-live-sessions" : "table-closed-sessions");
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const col of ["id", "kind", "profile / label", "url", "started"]) {
    const th = document.createElement("th");
    th.textContent = col;
    headRow.append(th);
  }
  if (!live) {
    const thActions = document.createElement("th");
    thActions.className = "col-actions";
    headRow.append(thActions);
  }
  thead.append(headRow);

  const tbody = document.createElement("tbody");
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.setAttribute("data-session-id", row.id);
    tr.append(
      cell(linkCell(row.id, `/sessions/${encodeURIComponent(row.id)}`)),
      cell(textCell(row.kind)),
      cell(textCell(row.label ?? row.profile ?? "")),
      cell(textCell(shortUrl(row.url, 80))),
      cell(textCell(formatDateTime(row.started_at))),
    );
    if (!live) {
      const actionTd = document.createElement("td");
      actionTd.className = "col-actions";
      const relaunchBtn = document.createElement("button");
      relaunchBtn.className = "row-action icon-btn";
      relaunchBtn.setAttribute("aria-label", "Relaunch with same params");
      relaunchBtn.setAttribute("title", "Relaunch like this — same kind/profile/url");
      relaunchBtn.textContent = "↻";
      relaunchBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        relaunchClosedSession(row.id);
      });
      const delBtn = document.createElement("button");
      delBtn.className = "row-action icon-btn--danger";
      delBtn.setAttribute("aria-label", "Delete recording");
      delBtn.setAttribute("title", "Delete recording files from disk");
      delBtn.textContent = "⊗";
      delBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        deleteSessionRecording(row.id);
      });
      actionTd.append(relaunchBtn, delBtn);
      tr.append(actionTd);
    }
    tbody.append(tr);
  }
  table.append(thead, tbody);
  const scroll = document.createElement("div");
  scroll.className = "table-scroll";
  scroll.append(table);
  return scroll;
}

let dashboardRoot: HTMLElement | null = null;
let activeDashboardDisposer: DashboardDisposer | null = null;

async function deleteSessionRecording(id: string): Promise<void> {
  try {
    const result = await deleteRecording(id);
    showSnackbar(`Deleted session ${id.slice(0, 8)}… (${result.files_removed} files removed)`);
    if (dashboardRoot) {
      const state = await loadState();
      renderDashboard(dashboardRoot, state);
    }
  } catch (err: unknown) {
    showSnackbar(`Delete failed: ${String(err)}`, true);
  }
}

async function relaunchClosedSession(id: string): Promise<void> {
  try {
    const result = await relaunchSession(id);
    showSnackbar(`Relaunched as ${result.id.slice(0, 8)}… (${result.kind})`);
    if (dashboardRoot) {
      const state = await loadState();
      renderDashboard(dashboardRoot, state);
    }
  } catch (err: unknown) {
    showSnackbar(`Relaunch failed: ${String(err)}`, true);
  }
}

async function startSavedScenario(name: string): Promise<void> {
  try {
    const result = await startScenario(name);
    showSnackbar(`Started '${name}' (${result.participants.length} participants)`);
    if (dashboardRoot) {
      const state = await loadState();
      renderDashboard(dashboardRoot, state);
    }
  } catch (err: unknown) {
    showSnackbar(`Start failed: ${String(err)}`, true);
  }
}

function cell(child: Node): HTMLTableCellElement {
  const td = document.createElement("td");
  td.append(child);
  return td;
}

function textCell(text: string): Text {
  return document.createTextNode(text);
}

function linkCell(text: string, href: string): HTMLAnchorElement {
  const a = document.createElement("a");
  a.href = href;
  a.textContent = text;
  return a;
}

function renderScenarioList(scenarios: LiveScenario[]): HTMLElement {
  if (scenarios.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No live scenarios.";
    return empty;
  }
  const ul = document.createElement("ul");
  ul.className = "scenario-list";
  for (const scenario of scenarios) {
    const li = document.createElement("li");
    li.className = "scenario-list__item";
    li.setAttribute("data-scenario-id", scenario.scenario_id);
    const title = document.createElement("div");
    title.className = "scenario-list__title";
    title.textContent = scenario.name;
    const chips = document.createElement("div");
    chips.className = "chips";
    for (const part of scenario.participants) {
      const chip = document.createElement("a");
      chip.className = `chip chip--${part.kind}`;
      chip.href = `/sessions/${encodeURIComponent(part.instance_id)}`;
      chip.textContent = `${part.role}: ${part.persona}`;
      chips.append(chip);
    }
    li.append(title, chips);
    ul.append(li);
  }
  return ul;
}

function renderSavedScenarios(scenarios: SavedScenario[]): HTMLElement {
  if (scenarios.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No saved scenarios on disk.";
    return empty;
  }
  const ul = document.createElement("ul");
  ul.className = "scenario-list";
  for (const s of [...scenarios].sort((a, b) => b.mtime - a.mtime)) {
    const li = document.createElement("li");
    li.className = "scenario-list__item saved-scenario";
    li.setAttribute("data-scenario-name", s.name);

    const main = document.createElement("div");
    main.className = "saved-scenario__main";
    const title = document.createElement("div");
    title.className = "scenario-list__title";
    title.textContent = s.name;
    const meta = document.createElement("div");
    meta.className = "saved-scenario__meta";
    meta.textContent = `${s.form} · ${formatDateTime(new Date(s.mtime * 1000).toISOString())}`;
    main.append(title, meta);

    const btn = document.createElement("button");
    btn.className = "btn btn--primary";
    btn.setAttribute("aria-label", `Start scenario ${s.name}`);
    btn.textContent = "▶ Start";
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      startSavedScenario(s.name);
    });

    li.append(main, btn);
    ul.append(li);
  }
  return ul;
}

function renderPersonaGrid(personas: PersonaSummary[]): HTMLElement {
  if (personas.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No personas saved.";
    return empty;
  }
  const grid = document.createElement("div");
  grid.className = "persona-grid";
  for (const p of personas) {
    const card = document.createElement("div");
    card.className = "persona-card";
    card.setAttribute("data-persona-name", p.name);

    const name = document.createElement("div");
    name.className = "persona-card__name";
    name.textContent = p.display_name ?? p.name;

    const engines = document.createElement("div");
    engines.className = "persona-card__engines";
    engines.textContent = p.engines.join(", ") || "no engines";

    const last = document.createElement("div");
    last.className = "persona-card__last";
    last.textContent = `last used ${formatDateTime(p.last_used)}`;

    const sizeEl = document.createElement("div");
    sizeEl.className = "persona-card__size";
    if (sizesLoaded) {
      const bytes = personaSizes[p.name];
      sizeEl.textContent = bytes != null ? formatBytes(bytes) : "—";
    } else {
      sizeEl.textContent = "…";
    }

    const actions = document.createElement("div");
    actions.className = "persona-card__actions";

    const editBtn = document.createElement("button");
    editBtn.className = "icon-btn";
    editBtn.setAttribute("aria-label", `Edit persona ${p.name}`);
    editBtn.setAttribute("title", "Edit YAML");
    editBtn.textContent = "✎";
    editBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      openPersonaEditor(p.name);
    });
    actions.append(editBtn);

    card.append(name, engines, last, sizeEl, actions);
    grid.append(card);
  }
  return grid;
}

function renderMacroList(state: DashboardState): HTMLElement {
  const macros = state.macros;
  if (macros.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No macros saved.";
    return empty;
  }
  const ul = document.createElement("ul");
  ul.className = "macro-list";
  for (const m of macros) {
    const li = document.createElement("li");
    li.className = "macro-list__item";
    li.setAttribute("data-macro-name", m.name);
    const name = document.createElement("div");
    name.className = "macro-list__name";
    name.textContent = m.name;
    const desc = document.createElement("div");
    desc.className = "macro-list__desc";
    desc.textContent = m.description ?? "";
    li.append(name, desc);
    if (m.parameters.length > 0) {
      const params = document.createElement("div");
      params.className = "macro-list__params";
      params.textContent = `params: ${m.parameters.join(", ")}`;
      li.append(params);
    }
    const actions = document.createElement("div");
    actions.className = "macro-list__actions";
    const editBtn = document.createElement("button");
    editBtn.className = "row-action icon-btn";
    editBtn.setAttribute("aria-label", `Edit macro ${m.name}`);
    editBtn.setAttribute("title", "Edit macro");
    editBtn.setAttribute("data-testid", `macro-edit-${m.name}`);
    editBtn.textContent = "✎";
    editBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      openMacroEditor(m.name, state.sessions);
    });

    const previewBtn = document.createElement("button");
    previewBtn.className = "row-action icon-btn";
    previewBtn.setAttribute("aria-label", `Preview repair suggestions for ${m.name}`);
    previewBtn.setAttribute("title", "Repair preview");
    previewBtn.setAttribute("data-testid", `macro-repair-preview-${m.name}`);
    previewBtn.textContent = "⚑";
    previewBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      openMacroRepairPreview(m.name);
    });
    actions.append(editBtn, previewBtn);
    li.append(actions);
    ul.append(li);
  }
  return ul;
}

// ─── helpers ──────────────────────────────────────────────────────────────────

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

export function formatBytes(n: number): string {
  if (n >= 1_073_741_824) return `${(n / 1_073_741_824).toFixed(1)} GB`;
  if (n >= 1_048_576) return `${(n / 1_048_576).toFixed(1)} MB`;
  if (n >= 1_024) return `${(n / 1_024).toFixed(0)} KB`;
  return `${n} B`;
}

// ─── boot ─────────────────────────────────────────────────────────────────────

export function disposeDashboard(): void {
  activeDashboardDisposer?.();
}

export async function bootDashboard(root: HTMLElement): Promise<DashboardDisposer> {
  disposeDashboard();
  dashboardRoot = root;
  log.info({ event: "dashboard_boot_start" });
  loadPersonaSizes();
  let disposed = false;
  let refreshGeneration = 0;
  let source: EventSource | null = null;
  let intervalId: ReturnType<typeof window.setInterval> | null = null;
  let currentState = EMPTY_STATE;

  const tick = async (scopes: ReadonlySet<DashboardScope> | null = null): Promise<void> => {
    const generation = ++refreshGeneration;
    const state = scopes ? await refreshScopedState(currentState, scopes) : await loadState();
    if (disposed || generation !== refreshGeneration) {
      return;
    }
    currentState = state;
    renderDashboard(root, state);
    log.debug({
      event: "dashboard_refresh",
      live_count: state.sessions.live.length,
      closed_count: state.sessions.closed.length,
      live_scenarios: state.scenarios.live.length,
      personas: state.personas.length,
      macros: state.macros.length,
    });
  };
  await tick();
  if (typeof EventSource !== "undefined") {
    source = new EventSource(dashboardEventsUrl());
    const refreshFromStream = (event?: MessageEvent) => {
      const scopes = parseInvalidateScopes(event?.data);
      tick(scopes).catch((err: unknown) => {
        log.warn({ event: "dashboard_stream_refresh_failed", error: String(err) });
      });
    };
    source.onmessage = refreshFromStream;
    source.addEventListener("invalidate", refreshFromStream);
    source.onerror = () => {
      log.warn({ event: "dashboard_stream_error" });
      source?.close();
      source = null;
    };
  }
  intervalId = window.setInterval(() => {
    tick().catch((err: unknown) => {
      log.warn({ event: "dashboard_refresh_failed", error: String(err) });
    });
  }, REFRESH_MS);
  const dispose = (): void => {
    if (disposed) return;
    disposed = true;
    refreshGeneration++;
    source?.close();
    source = null;
    if (intervalId !== null) {
      window.clearInterval(intervalId);
      intervalId = null;
    }
    if (dashboardRoot === root) {
      dashboardRoot = null;
    }
    if (activeDashboardDisposer === dispose) {
      activeDashboardDisposer = null;
    }
  };
  activeDashboardDisposer = dispose;
  log.info({ event: "dashboard_boot_complete", refresh_ms: REFRESH_MS });
  return dispose;
}

if (typeof document !== "undefined") {
  initTelemetry({ pageName: "dashboard" });
  log.info({ event: "page_load", page: "dashboard" });
  if (typeof window !== "undefined") {
    window.addEventListener("beforeunload", () => {
      log.info({ event: "page_unload", page: "dashboard" });
      disposeDashboard();
    });
  }
  const root = document.getElementById("app");
  if (root) {
    bootDashboard(root).catch((err: unknown) => {
      log.error({ event: "dashboard_boot_failed", error: String(err) });
      root.textContent = `Dashboard failed: ${(err as Error).message}`;
    });
  } else {
    log.warn({ event: "dashboard_root_missing" });
  }
}
