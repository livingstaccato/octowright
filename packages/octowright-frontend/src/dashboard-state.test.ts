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

  it("falls back per slice when loaders reject", async () => {
    apiMocks.getSessions.mockRejectedValueOnce(new Error("sessions"));
    apiMocks.getScenarios.mockRejectedValueOnce(new Error("scenarios"));
    apiMocks.getPersonas.mockRejectedValueOnce(new Error("personas"));
    apiMocks.getMacros.mockRejectedValueOnce(new Error("macros"));

    await expect(loadState()).resolves.toEqual(EMPTY_STATE);
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

  it("falls back to empty slices when scoped refreshes reject", async () => {
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
      } as never,
      new Set(["sessions", "scenarios", "personas", "macros"]),
    );

    expect(next).toEqual(EMPTY_STATE);
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
