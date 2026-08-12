// Dashboard composition: assembles the panel registry, owns the module-
// level mount state (dashboardRoot, dashboardPanels), wires user-action
// refreshes through refreshDashboardNow, and runs the SSE invalidation
// stream + polling fallback.
//
// Per-concern logic lives in sibling modules (snackbar, persona-editor,
// macro-editor, macro-renderers, macro-list, session-table,
// scenario-panels, persona-grid, dashboard-state, dashboard-panels).

import { deleteRecording, getPersonaSizes, relaunchSession, startScenario } from "./api.js";
import { bootstrapDashboardAuth, DASHBOARD_AUTH_REQUIRED_EVENT, isolateDashboardTabAuth } from "./dashboard-auth.js";
import type { DashboardEventStreamHandle } from "./dashboard-events.js";
import { openDashboardEventStream } from "./dashboard-events.js";
import type { PanelDef, PanelInstance } from "./dashboard-panels.js";
import { mountPanels, updatePanels } from "./dashboard-panels.js";
import type { DashboardScope, DashboardState } from "./dashboard-state.js";
import { EMPTY_STATE, loadState, parseInvalidateScopes, refreshScopedState } from "./dashboard-state.js";
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

const DASHBOARD_SCOPE_LABELS: ReadonlyArray<[DashboardScope, string]> = [
  ["sessions", "Sessions"],
  ["scenarios", "Scenarios"],
  ["personas", "Personas"],
  ["macros", "Macros"],
];

export type DashboardDisposer = () => void;
export type DashboardPanel = PanelInstance<DashboardScope, DashboardState>;

export type { DashboardScope, DashboardState } from "./dashboard-state.js";
export { loadState } from "./dashboard-state.js";
export { formatBytes } from "./format.js";
export { openMacroRepairPreview } from "./macro-editor.js";
export { openPersonaEditor } from "./persona-editor.js";
// ─── re-exports for the public package surface ───────────────────────────────
// These aren't shims; they're the dashboard-package barrel. Each implementation
// lives in its own per-concern module above.
export { showSnackbar } from "./snackbar.js";

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
let dashboardCurrentState: DashboardState = EMPTY_STATE;
let activeDashboardDisposer: DashboardDisposer | null = null;

/**
 * Refresh the dashboard after a user-initiated action. Reuses the existing
 * panel registry so wrappers, headings, listeners, and <details> open state
 * survive — and so a subsequent SSE invalidation updates the live DOM
 * instead of orphaned nodes.
 */
export async function refreshDashboardNow(): Promise<void> {
  if (!dashboardRoot) return;
  const state = await loadState(dashboardCurrentState);
  dashboardCurrentState = state;
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
    isDegraded: (s) => s.errors?.has("sessions") ?? false,
    testid: "live-browsers",
    // "Live sessions" (not "browsers"): the live pool now also holds terminal
    // sessions (octowright[terminal] extra). testid stays for existing tests.
    title: "Live sessions",
    buildBody: (s) =>
      renderSessionTable(s.sessions.live, true, {
        onRelaunch: (id) => void relaunchClosedSession(id),
        onDelete: (id) => void deleteSessionRecording(id),
      }),
  },
  {
    scope: "scenarios",
    isDegraded: (s) => s.errors?.has("scenarios") ?? false,
    testid: "live-scenarios",
    title: "Live scenarios",
    buildBody: (s) => renderScenarioList(s.scenarios.live),
  },
  {
    scope: "personas",
    isDegraded: (s) => s.errors?.has("personas") ?? false,
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
    isDegraded: (s) => s.errors?.has("scenarios") ?? false,
    testid: "saved-scenarios",
    title: "Saved scenarios",
    buildBody: (s) => renderSavedScenarios(s.scenarios.saved ?? [], (name) => void startSavedScenario(name)),
  },
  {
    scope: "sessions",
    isDegraded: (s) => s.errors?.has("sessions") ?? false,
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
    isDegraded: (s) => s.errors?.has("macros") ?? false,
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
  const panels = mountPanels(root, PANEL_DEFS, state);
  updateDegradedNotice(root, state);
  return panels;
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
  const root = panels[0]?.root.parentElement;
  if (root) updateDegradedNotice(root, state);
}

function updateDegradedNotice(root: HTMLElement, state: DashboardState): void {
  const existing = root.querySelector<HTMLElement>('[data-testid="dashboard-degraded"]');
  const errors = state.errors ?? new Set<DashboardScope>();
  if (errors.size === 0) {
    existing?.remove();
    return;
  }

  const labels = DASHBOARD_SCOPE_LABELS.filter(([scope]) => errors.has(scope)).map(([, label]) => label);
  const notice = existing ?? document.createElement("div");
  notice.className = "dashboard-degraded";
  notice.setAttribute("data-testid", "dashboard-degraded");
  notice.setAttribute("role", "status");
  notice.setAttribute("aria-live", "polite");
  notice.textContent = `Some dashboard data is unavailable or stale: ${labels.join(", ")}. Retrying automatically.`;
  if (!existing) root.insertBefore(notice, root.firstChild);
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
  let disposed = false;
  let source: DashboardEventStreamHandle | null = null;
  let intervalId: ReturnType<typeof window.setInterval> | null = null;
  dashboardCurrentState = EMPTY_STATE;
  let streamHealthy = false;
  let authBlocked = false;
  let refreshErrorShown = false;
  // Serialize tick() so concurrent SSE invalidations don't lose updates.
  let pendingTick: Promise<void> = Promise.resolve();

  const runTick = async (scopes: ReadonlySet<DashboardScope> | null): Promise<void> => {
    if (disposed) return;
    const state = scopes
      ? await refreshScopedState(dashboardCurrentState, scopes)
      : await loadState(dashboardCurrentState);
    if (disposed) return;
    dashboardCurrentState = state;
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
    pendingTick = pendingTick
      .then(() => runTick(scopes))
      .catch((err: unknown) => {
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

  const authRequired = (): void => {
    if (authBlocked) return;
    authBlocked = true;
    source?.close();
    source = null;
    stopPolling();
    showSnackbar("Dashboard pairing expired. Run `octowright dashboard` and open the new URL.", true);
  };
  window.addEventListener(DASHBOARD_AUTH_REQUIRED_EVENT, authRequired);

  const newlyPaired = await bootstrapDashboardAuth();
  if (newlyPaired) authBlocked = false;
  await isolateDashboardTabAuth();

  await tick();
  if (authBlocked) {
    const dispose = (): void => {
      if (disposed) return;
      disposed = true;
      stopPolling();
      window.removeEventListener(DASHBOARD_AUTH_REQUIRED_EVENT, authRequired);
      if (dashboardRoot === root) {
        dashboardRoot = null;
        dashboardPanels = null;
        dashboardCurrentState = EMPTY_STATE;
      }
      if (activeDashboardDisposer === dispose) activeDashboardDisposer = null;
    };
    activeDashboardDisposer = dispose;
    return dispose;
  }
  loadPersonaSizes();
  source = openDashboardEventStream({
    onOpen: () => {
      streamHealthy = true;
      stopPolling();
    },
    onInvalidate: (data) => {
      const scopes = parseInvalidateScopes(data);
      tick(scopes).catch((err: unknown) => {
        log.warn({ event: "dashboard_stream_refresh_failed", error: String(err) });
      });
    },
    onError: () => {
      log.warn({ event: "dashboard_stream_error" });
      streamHealthy = false;
      startPolling();
    },
  });
  if (!streamHealthy) {
    startPolling();
  }
  const dispose = (): void => {
    if (disposed) return;
    disposed = true;
    source?.close();
    source = null;
    stopPolling();
    window.removeEventListener(DASHBOARD_AUTH_REQUIRED_EVENT, authRequired);
    if (dashboardRoot === root) {
      dashboardRoot = null;
      dashboardPanels = null;
      dashboardCurrentState = EMPTY_STATE;
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
