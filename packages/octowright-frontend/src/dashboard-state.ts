// Dashboard state shape, loaders, and SSE invalidation-scope parsing. Pure
// logic; no DOM. dashboard.ts and the panel renderers all consume from here.

import { getMacros, getPersonas, getScenarios, getSessions } from "./api.js";
import type { MacroSummary, PersonaSummary, ScenarioListResponse, SessionListResponse } from "./types.js";

export type DashboardScope = "sessions" | "scenarios" | "personas" | "macros";

export interface DashboardState {
  sessions: SessionListResponse;
  scenarios: ScenarioListResponse;
  personas: PersonaSummary[];
  macros: MacroSummary[];
  /**
   * Scopes whose most recent fetch failed. A failed refresh keeps the
   * last-known slice data (so a backend 500 doesn't wipe the panels) and flags
   * the scope here for the UI to render a degraded/stale indicator.
   */
  errors: ReadonlySet<DashboardScope>;
}

export const EMPTY_STATE: DashboardState = {
  sessions: { live: [], closed: [] },
  scenarios: { live: [] },
  personas: [],
  macros: [],
  errors: new Set(),
};

export async function loadState(): Promise<DashboardState> {
  const errors = new Set<DashboardScope>();
  const [sessions, scenarios, personas, macros] = await Promise.all([
    getSessions().catch(() => {
      errors.add("sessions");
      return EMPTY_STATE.sessions;
    }),
    getScenarios().catch(() => {
      errors.add("scenarios");
      return EMPTY_STATE.scenarios;
    }),
    getPersonas().catch(() => {
      errors.add("personas");
      return EMPTY_STATE.personas;
    }),
    getMacros().catch(() => {
      errors.add("macros");
      return EMPTY_STATE.macros;
    }),
  ]);
  return { sessions, scenarios, personas, macros, errors };
}

export async function refreshScopedState(
  current: DashboardState,
  scopes: ReadonlySet<DashboardScope>,
): Promise<DashboardState> {
  // On failure we KEEP current[scope] (last-known) rather than blanking it, and
  // flag the scope in `errors`; a successful refetch clears the flag.
  const errors = new Set<DashboardScope>(current.errors);
  const next: DashboardState = { ...current, errors };
  const jobs: Promise<void>[] = [];
  if (scopes.has("sessions")) {
    jobs.push(
      getSessions()
        .then((sessions) => {
          next.sessions = sessions;
          errors.delete("sessions");
        })
        .catch(() => {
          errors.add("sessions");
        }),
    );
  }
  if (scopes.has("scenarios")) {
    jobs.push(
      getScenarios()
        .then((scenarios) => {
          next.scenarios = scenarios;
          errors.delete("scenarios");
        })
        .catch(() => {
          errors.add("scenarios");
        }),
    );
  }
  if (scopes.has("personas")) {
    jobs.push(
      getPersonas()
        .then((personas) => {
          next.personas = personas;
          errors.delete("personas");
        })
        .catch(() => {
          errors.add("personas");
        }),
    );
  }
  if (scopes.has("macros")) {
    jobs.push(
      getMacros()
        .then((macros) => {
          next.macros = macros;
          errors.delete("macros");
        })
        .catch(() => {
          errors.add("macros");
        }),
    );
  }
  await Promise.all(jobs);
  return next;
}

/**
 * Parse the SSE invalidation payload's optional ``scope`` field into a
 * narrowed scope set, or null if every slice should refetch.
 *
 * The server sends ``{"scope":"sessions"}`` or ``{"scope":"sessions,macros"}``.
 * Unknown tokens are silently dropped; if the result is empty (or the payload
 * is missing/malformed), null is returned so the caller does a full refresh.
 */
export function parseInvalidateScopes(data: string | null | undefined): ReadonlySet<DashboardScope> | null {
  if (!data) return null;
  try {
    const parsed = JSON.parse(data) as { scope?: unknown };
    const raw = parsed.scope;
    if (typeof raw !== "string") return null;
    const scopes = new Set<DashboardScope>();
    for (const part of raw.split(",").map((p) => p.trim()).filter(Boolean)) {
      if (part === "sessions" || part === "scenarios" || part === "personas" || part === "macros") {
        scopes.add(part);
      }
    }
    return scopes.size > 0 ? scopes : null;
  } catch {
    return null;
  }
}
