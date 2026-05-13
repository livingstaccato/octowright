// Dashboard composition: assembles the panel registry, owns the module-
// level mount state (dashboardRoot, dashboardPanels), wires user-action
// refreshes through refreshDashboardNow, and runs the SSE invalidation
// stream + polling fallback.
//
// Per-concern logic lives in sibling modules (snackbar, persona-editor,
// macro-editor, macro-renderers, macro-list, session-table,
// scenario-panels, persona-grid, dashboard-state, dashboard-panels).

import { dashboardEventsUrl, deleteRecording, getPersonaSizes, relaunchSession, startScenario } from "./api.js";
import { mountPanels, updatePanels } from "./dashboard-panels.js";
import type { PanelDef, PanelInstance } from "./dashboard-panels.js";
import {
  EMPTY_STATE,
  loadState,
  parseInvalidateScopes,
  refreshScopedState,
} from "./dashboard-state.js";
import type { DashboardScope, DashboardState } from "./dashboard-state.js";
import { openMacroEditor, openMacroRepairPreview } from "./macro-editor.js";
import { renderMacroList } from "./macro-list.js";
import { openPersonaEditor } from "./persona-editor.js";
import { renderPersonaGrid } from "./persona-grid.js";
import { renderSavedScenarios, renderScenarioList } from "./scenario-panels.js";
import { renderSessionTable } from "./session-table.js";
import { showSnackbar } from "./snackbar.js";
import { getLogger, initTelemetry } from "./telemetry.js";

const REFRESH_MS = 5000;

const log = getLogger("octowright.frontend.dashboard");

export type DashboardDisposer = () => void;
export type DashboardPanel = PanelInstance<DashboardScope, DashboardState>;

// ─── re-exports for the public package surface ───────────────────────────────
// These aren't shims; they're the dashboard-package barrel. Each implementation
// lives in its own per-concern module above.
export { showSnackbar } from "./snackbar.js";
export { openPersonaEditor } from "./persona-editor.js";
export { openMacroRepairPreview } from "./macro-editor.js";
export { formatBytes } from "./format.js";
export { loadState } from "./dashboard-state.js";
export type { DashboardScope, DashboardState } from "./dashboard-state.js";

// ─── persona-size cache (used by the persona panel) ──────────────────────────

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

// ─── dashboard mount state + action handlers ─────────────────────────────────

let dashboardRoot: HTMLElement | null = null;
let dashboardPanels: DashboardPanel[] | null = null;
let activeDashboardDisposer: DashboardDisposer | null = null;

/**
 * Refresh the dashboard after a user-initiated action. Reuses the existing
 * panel registry so wrappers, headings, listeners, and <details> open state
 * survive — and so a subsequent SSE invalidation updates the live DOM
 * instead of orphaned nodes.
 */
export async function refreshDashboardNow(): Promise<void> {
  if (!dashboardRoot) return;
  const state = await loadState();
  if (dashboardPanels === null) {
    dashboardPanels = renderDashboard(dashboardRoot, state);
  } else {
    updateDashboard(dashboardPanels, state, null);
  }
}

const fireRefresh = (): void => {
  void refreshDashboardNow();
};

async function deleteSessionRecording(id: string): Promise<void> {
  try {
    const result = await deleteRecording(id);
    showSnackbar(`Deleted session ${id.slice(0, 8)}… (${result.files_removed} files removed)`);
    await refreshDashboardNow();
  } catch (err: unknown) {
    showSnackbar(`Delete failed: ${String(err)}`, true);
  }
}

async function relaunchClosedSession(id: string): Promise<void> {
  try {
    const result = await relaunchSession(id);
    showSnackbar(`Relaunched as ${result.id.slice(0, 8)}… (${result.kind})`);
    await refreshDashboardNow();
  } catch (err: unknown) {
    showSnackbar(`Relaunch failed: ${String(err)}`, true);
  }
}

async function startSavedScenario(name: string): Promise<void> {
  try {
    const result = await startScenario(name);
    showSnackbar(`Started '${name}' (${result.participants.length} participants)`);
    await refreshDashboardNow();
  } catch (err: unknown) {
    showSnackbar(`Start failed: ${String(err)}`, true);
  }
}

// ─── panel registry ─────────────────────────────────────────────────────────

const PANEL_DEFS: ReadonlyArray<PanelDef<DashboardScope, DashboardState>> = [
  {
    scope: "sessions",
    testid: "live-browsers",
    title: "Live browsers",
    buildBody: (s) =>
      renderSessionTable(s.sessions.live, true, {
        onRelaunch: (id) => void relaunchClosedSession(id),
        onDelete: (id) => void deleteSessionRecording(id),
      }),
  },
  {
    scope: "scenarios",
    testid: "live-scenarios",
    title: "Live scenarios",
    buildBody: (s) => renderScenarioList(s.scenarios.live),
  },
  {
    scope: "personas",
    testid: "personas",
    title: "Personas",
    buildBody: (s) =>
      renderPersonaGrid(s.personas, {
        sizesProvider: () => ({ sizes: personaSizes, loaded: sizesLoaded }),
        onEdit: openPersonaEditor,
      }),
  },
  {
    scope: "scenarios",
    testid: "saved-scenarios",
    title: "Saved scenarios",
    buildBody: (s) =>
      renderSavedScenarios(s.scenarios.saved ?? [], (name) => void startSavedScenario(name)),
  },
  {
    scope: "sessions",
    testid: "closed-sessions",
    title: "Recent closed sessions",
    buildBody: (s) =>
      renderSessionTable(s.sessions.closed.slice(0, 20), false, {
        onRelaunch: (id) => void relaunchClosedSession(id),
        onDelete: (id) => void deleteSessionRecording(id),
      }),
  },
  {
    scope: "macros",
    testid: "macros",
    title: "Macros",
    collapsible: true,
    defaultOpen: false,
    buildBody: (s) =>
      renderMacroList(s, {
        onEdit: (name, sessions) => openMacroEditor(name, sessions, fireRefresh),
        onRepairPreview: openMacroRepairPreview,
      }),
  },
];

/**
 * Mount the full dashboard tree under ``root`` and return the panel
 * registry. Open <details> state from any prior mount is preserved.
 */
export function renderDashboard(root: HTMLElement, state: DashboardState): DashboardPanel[] {
  return mountPanels(root, PANEL_DEFS, state);
}

/**
 * Re-render only the panels whose scope is in ``scopes`` (or every panel
 * if ``scopes`` is null). Wrappers, headings, and <details> open state
 * are preserved.
 */
export function updateDashboard(
  panels: ReadonlyArray<DashboardPanel>,
  state: DashboardState,
  scopes: ReadonlySet<DashboardScope> | null,
): void {
  updatePanels(panels, state, scopes);
}

// ─── boot ────────────────────────────────────────────────────────────────────

export function disposeDashboard(): void {
  activeDashboardDisposer?.();
}

export async function bootDashboard(root: HTMLElement): Promise<DashboardDisposer> {
  disposeDashboard();
  dashboardRoot = root;
  log.info({ event: "dashboard_boot_start" });
  initTelemetry({ pageName: "dashboard" });
  loadPersonaSizes();
  let disposed = false;
  let source: EventSource | null = null;
  let intervalId: ReturnType<typeof window.setInterval> | null = null;
  let currentState = EMPTY_STATE;
  let streamHealthy = false;
  let refreshErrorShown = false;
  // Serialize tick() so concurrent SSE invalidations don't lose updates.
  let pendingTick: Promise<void> = Promise.resolve();

  const runTick = async (scopes: ReadonlySet<DashboardScope> | null): Promise<void> => {
    if (disposed) return;
    const state = scopes ? await refreshScopedState(currentState, scopes) : await loadState();
    if (disposed) return;
    currentState = state;
    if (dashboardPanels === null) {
      dashboardPanels = renderDashboard(root, state);
    } else if (scopes === null) {
      updateDashboard(dashboardPanels, state, null);
    } else {
      updateDashboard(dashboardPanels, state, scopes);
    }
    log.debug({
      event: "dashboard_refresh",
      live_count: state.sessions.live.length,
      closed_count: state.sessions.closed.length,
      live_scenarios: state.scenarios.live.length,
      personas: state.personas.length,
      macros: state.macros.length,
    });
    refreshErrorShown = false;
  };

  const tick = (scopes: ReadonlySet<DashboardScope> | null = null): Promise<void> => {
    pendingTick = pendingTick.then(() => runTick(scopes)).catch((err: unknown) => {
      log.warn({ event: "dashboard_tick_failed", error: String(err) });
      if (!refreshErrorShown) {
        showSnackbar(`Dashboard refresh failed: ${String(err)}`, true);
        refreshErrorShown = true;
      }
    });
    return pendingTick;
  };

  const stopPolling = (): void => {
    if (intervalId !== null) {
      window.clearInterval(intervalId);
      intervalId = null;
    }
  };
  const startPolling = (): void => {
    if (intervalId !== null || disposed) return;
    intervalId = window.setInterval(() => {
      tick().catch((err: unknown) => {
        log.warn({ event: "dashboard_refresh_failed", error: String(err) });
      });
    }, REFRESH_MS);
  };

  await tick();
  if (typeof EventSource !== "undefined") {
    source = new EventSource(dashboardEventsUrl());
    streamHealthy = true;
    const refreshFromStream = (event?: MessageEvent) => {
      const scopes = parseInvalidateScopes(event?.data);
      tick(scopes).catch((err: unknown) => {
        log.warn({ event: "dashboard_stream_refresh_failed", error: String(err) });
      });
    };
    source.addEventListener("invalidate", refreshFromStream);
    source.onerror = () => {
      log.warn({ event: "dashboard_stream_error" });
      source?.close();
      source = null;
      streamHealthy = false;
      startPolling();
    };
  }
  if (!streamHealthy) {
    startPolling();
  }
  const dispose = (): void => {
    if (disposed) return;
    disposed = true;
    source?.close();
    source = null;
    stopPolling();
    if (dashboardRoot === root) {
      dashboardRoot = null;
      dashboardPanels = null;
    }
    if (activeDashboardDisposer === dispose) {
      activeDashboardDisposer = null;
    }
  };
  activeDashboardDisposer = dispose;
  return dispose;
}

export function bootDashboardFromDom(doc: Document = document): void {
  const root = doc.getElementById("app");
  if (!root) {
    log.warn({ event: "dashboard_root_missing" });
    return;
  }
  bootDashboard(root).catch((err: unknown) => {
    log.error({ event: "dashboard_boot_failed", error: String(err) });
    root.textContent = `Dashboard failed to load: ${String(err)}`;
  });
}

if (typeof document !== "undefined") {
  bootDashboardFromDom(document);
}
