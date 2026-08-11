import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getMacros: vi.fn(),
  getPersonas: vi.fn(),
  getScenarios: vi.fn(),
  getSessions: vi.fn(),
}));

vi.mock("./api.js", () => apiMocks);

const { EMPTY_STATE, loadState, parseInvalidateScopes, refreshScopedState } = await import("./dashboard-state.js");

function resetApiMocks(): void {
  apiMocks.getSessions.mockReset().mockResolvedValue({ live: [{ id: "live" }], closed: [] });
  apiMocks.getScenarios.mockReset().mockResolvedValue({ live: [{ name: "scenario" }] });
  apiMocks.getPersonas.mockReset().mockResolvedValue([{ name: "persona" }]);
  apiMocks.getMacros.mockReset().mockResolvedValue([{ name: "macro" }]);
}

beforeEach(() => {
  resetApiMocks();
});

describe("loadState", () => {
  it("loads every dashboard slice", async () => {
    const state = await loadState();
    expect(state.sessions.live[0]?.id).toBe("live");
    expect(state.scenarios.live[0]?.name).toBe("scenario");
    expect(state.personas[0]?.name).toBe("persona");
    expect(state.macros[0]?.name).toBe("macro");
  });

  it("keeps empty slices but flags errored slices when loaders reject", async () => {
    apiMocks.getSessions.mockRejectedValueOnce(new Error("sessions"));
    apiMocks.getScenarios.mockRejectedValueOnce(new Error("scenarios"));
    apiMocks.getPersonas.mockRejectedValueOnce(new Error("personas"));
    apiMocks.getMacros.mockRejectedValueOnce(new Error("macros"));

    const state = await loadState();
    expect(state.sessions).toEqual(EMPTY_STATE.sessions);
    expect(state.scenarios).toEqual(EMPTY_STATE.scenarios);
    expect(state.personas).toEqual([]);
    expect(state.macros).toEqual([]);
    expect([...state.errors].sort()).toEqual(["macros", "personas", "scenarios", "sessions"]);
  });

  it("reports no errors when every loader resolves", async () => {
    const state = await loadState();
    expect([...state.errors]).toEqual([]);
  });
});

describe("refreshScopedState", () => {
  it("returns an unchanged copy for an empty scope set", async () => {
    const current = { ...EMPTY_STATE };
    const next = await refreshScopedState(current, new Set());
    expect(next).toEqual(current);
    expect(next).not.toBe(current);
    expect(apiMocks.getSessions).not.toHaveBeenCalled();
  });

  it("refreshes all requested scopes", async () => {
    const next = await refreshScopedState(EMPTY_STATE, new Set(["sessions", "scenarios", "personas", "macros"]));
    expect(next.sessions.live[0]?.id).toBe("live");
    expect(next.scenarios.live[0]?.name).toBe("scenario");
    expect(next.personas[0]?.name).toBe("persona");
    expect(next.macros[0]?.name).toBe("macro");
  });

  it("keeps last-known slices and flags errors when scoped refreshes reject", async () => {
    apiMocks.getSessions.mockRejectedValueOnce(new Error("sessions"));
    apiMocks.getScenarios.mockRejectedValueOnce(new Error("scenarios"));
    apiMocks.getPersonas.mockRejectedValueOnce(new Error("personas"));
    apiMocks.getMacros.mockRejectedValueOnce(new Error("macros"));

    const next = await refreshScopedState(
      {
        sessions: { live: [{ id: "stale" }], closed: [] },
        scenarios: { live: [{ name: "stale" }] },
        personas: [{ name: "stale" }],
        macros: [{ name: "stale" }],
        errors: new Set(),
      } as never,
      new Set(["sessions", "scenarios", "personas", "macros"]),
    );

    // A backend 500 must NOT wipe the panels; last-known data stays visible.
    expect(next.sessions.live[0]?.id).toBe("stale");
    expect(next.scenarios.live[0]?.name).toBe("stale");
    expect(next.personas[0]?.name).toBe("stale");
    expect(next.macros[0]?.name).toBe("stale");
    expect([...next.errors].sort()).toEqual(["macros", "personas", "scenarios", "sessions"]);
  });

  it("clears a slice error once its refresh succeeds again", async () => {
    const next = await refreshScopedState(
      { ...EMPTY_STATE, errors: new Set(["sessions"]) } as never,
      new Set(["sessions"]),
    );
    expect(next.sessions.live[0]?.id).toBe("live");
    expect([...next.errors]).toEqual([]);
  });
});

describe("parseInvalidateScopes", () => {
  it("returns null for missing, malformed, non-string, or unknown scopes", () => {
    expect(parseInvalidateScopes(null)).toBeNull();
    expect(parseInvalidateScopes("")).toBeNull();
    expect(parseInvalidateScopes("{")).toBeNull();
    expect(parseInvalidateScopes('{"scope":42}')).toBeNull();
    expect(parseInvalidateScopes('{"scope":"unknown"}')).toBeNull();
  });

  it("trims known scopes and drops unknown tokens", () => {
    const scopes = parseInvalidateScopes('{"scope":" sessions, unknown,macros "}');
    expect(scopes).not.toBeNull();
    expect(Array.from(scopes ?? [])).toEqual(["sessions", "macros"]);
  });
});
