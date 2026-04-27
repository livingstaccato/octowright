import {
  deleteRecording,
  getMacros,
  getPersonaDetail,
  getPersonas,
  getPersonaSizes,
  getScenarios,
  getSessions,
  relaunchSession,
  startScenario,
  updatePersonaYaml,
} from "./api.js";
import { formatDateTime, shortUrl } from "./format.js";
import { getLogger, initTelemetry } from "./telemetry.js";
import type {
  LiveScenario,
  MacroSummary,
  PersonaSummary,
  SavedScenario,
  ScenarioListResponse,
  SessionListResponse,
  SessionSummary,
} from "./types.js";

const REFRESH_MS = 5000;

const log = getLogger("octowright.frontend.dashboard");

interface DashboardState {
  sessions: SessionListResponse;
  scenarios: ScenarioListResponse;
  personas: PersonaSummary[];
  macros: MacroSummary[];
}

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

// ─── render ───────────────────────────────────────────────────────────────────

export function renderDashboard(root: HTMLElement, state: DashboardState): void {
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
    section("Macros", "macros", renderMacroList(state.macros), { collapsible: true }),
  );
}

function section(
  title: string,
  testid: string,
  body: HTMLElement,
  opts: { collapsible?: boolean } = {},
): HTMLElement {
  const wrapper = opts.collapsible ? document.createElement("details") : document.createElement("section");
  wrapper.className = `panel panel--${testid}`;
  wrapper.setAttribute("data-testid", `panel-${testid}`);
  if (opts.collapsible && wrapper instanceof HTMLDetailsElement) {
    wrapper.open = false;
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

function renderMacroList(macros: MacroSummary[]): HTMLElement {
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

export async function bootDashboard(root: HTMLElement): Promise<void> {
  dashboardRoot = root;
  log.info({ event: "dashboard_boot_start" });
  loadPersonaSizes();
  const tick = async (): Promise<void> => {
    const state = await loadState();
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
  window.setInterval(() => {
    tick().catch((err: unknown) => {
      log.warn({ event: "dashboard_refresh_failed", error: String(err) });
    });
  }, REFRESH_MS);
  log.info({ event: "dashboard_boot_complete", refresh_ms: REFRESH_MS });
}

if (typeof document !== "undefined") {
  initTelemetry({ pageName: "dashboard" });
  log.info({ event: "page_load", page: "dashboard" });
  if (typeof window !== "undefined") {
    window.addEventListener("beforeunload", () => {
      log.info({ event: "page_unload", page: "dashboard" });
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
