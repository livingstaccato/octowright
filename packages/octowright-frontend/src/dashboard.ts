import { getMacros, getPersonas, getScenarios, getSessions } from "./api.js";
import { formatDateTime, shortUrl } from "./format.js";
import { getLogger, initTelemetry } from "./telemetry.js";
import type {
  LiveScenario,
  MacroSummary,
  PersonaSummary,
  SessionListResponse,
  SessionSummary,
  ScenarioListResponse,
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

export async function loadState(): Promise<DashboardState> {
  const [sessions, scenarios, personas, macros] = await Promise.all([
    getSessions().catch(() => EMPTY_STATE.sessions),
    getScenarios().catch(() => EMPTY_STATE.scenarios),
    getPersonas().catch<PersonaSummary[]>(() => []),
    getMacros().catch<MacroSummary[]>(() => []),
  ]);
  return { sessions, scenarios, personas, macros };
}

export function renderDashboard(root: HTMLElement, state: DashboardState): void {
  root.innerHTML = "";
  root.append(
    section("Live browsers", "live-browsers", renderSessionTable(state.sessions.live, true)),
    section("Live scenarios", "live-scenarios", renderScenarioList(state.scenarios.live)),
    section("Personas", "personas", renderPersonaGrid(state.personas)),
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
  thead.innerHTML =
    "<tr><th>id</th><th>kind</th><th>profile / label</th><th>url</th><th>started</th></tr>";
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
    tbody.append(tr);
  }
  table.append(thead, tbody);
  return table;
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
    engines.textContent = p.engines.join(", ");
    const last = document.createElement("div");
    last.className = "persona-card__last";
    last.textContent = `last used ${formatDateTime(p.last_used)}`;
    card.append(name, engines, last);
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

export async function bootDashboard(root: HTMLElement): Promise<void> {
  log.info({ event: "dashboard_boot_start" });
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
