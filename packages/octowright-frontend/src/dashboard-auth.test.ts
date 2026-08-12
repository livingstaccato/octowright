import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  bootstrapDashboardAuth,
  clearDashboardBearer,
  DASHBOARD_AUTH_REQUIRED_EVENT,
  dashboardAuthHeaders,
  dashboardWebSocketProtocols,
  downloadDashboardMedia,
  fetchDashboardMediaObjectUrl,
  getDashboardBearer,
  handleDashboardUnauthorized,
  setDashboardBearer,
} from "./dashboard-auth.js";

const nowSeconds = 2_000_000_000;

beforeEach(() => {
  sessionStorage.clear();
  const localValues = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    get length() {
      return localValues.size;
    },
    clear: () => localValues.clear(),
    getItem: (key: string) => localValues.get(key) ?? null,
    key: (index: number) => [...localValues.keys()][index] ?? null,
    removeItem: (key: string) => localValues.delete(key),
    setItem: (key: string, value: string) => localValues.set(key, value),
  } satisfies Storage);
  vi.spyOn(Date, "now").mockReturnValue(nowSeconds * 1000);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("dashboard bearer storage", () => {
  it("stores a live bearer only in current-origin sessionStorage", () => {
    setDashboardBearer({ bearer: "secret-bearer", expires_at: nowSeconds + 60 });
    expect(getDashboardBearer()).toBe("secret-bearer");
    expect(localStorage.length).toBe(0);
    expect(document.cookie).not.toContain("secret-bearer");
  });

  it("drops expired or malformed records", () => {
    setDashboardBearer({ bearer: "expired", expires_at: nowSeconds - 1 });
    expect(getDashboardBearer()).toBeNull();
    expect(sessionStorage.length).toBe(0);

    sessionStorage.setItem("octowright.dashboard.auth.v1", "not-json");
    expect(getDashboardBearer()).toBeNull();
    expect(sessionStorage.length).toBe(0);
  });

  it("merges Authorization with caller headers", () => {
    setDashboardBearer({ bearer: "secret-bearer", expires_at: nowSeconds + 60 });
    const headers = dashboardAuthHeaders({ "X-Custom": "present" });
    expect(headers.get("Authorization")).toBe("Bearer secret-bearer");
    expect(headers.get("X-Custom")).toBe("present");
  });

  it("returns stable and private websocket protocols without changing a URL", () => {
    setDashboardBearer({ bearer: "abc_DEF-123", expires_at: nowSeconds + 60 });
    expect(dashboardWebSocketProtocols()).toEqual(["octowright.dashboard", "octowright.dashboard.bearer.abc_DEF-123"]);
  });

  it("clears an authenticated bearer and emits one re-pair event after 401", () => {
    setDashboardBearer({ bearer: "secret-bearer", expires_at: nowSeconds + 60 });
    const listener = vi.fn();
    window.addEventListener(DASHBOARD_AUTH_REQUIRED_EVENT, listener);
    expect(handleDashboardUnauthorized()).toBe(true);
    expect(getDashboardBearer()).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);
    expect(handleDashboardUnauthorized()).toBe(false);
    window.removeEventListener(DASHBOARD_AUTH_REQUIRED_EVENT, listener);
  });
});

describe("bootstrapDashboardAuth", () => {
  it("scrubs, redeems, stores, and removes the pairing code from history", async () => {
    const replaceState = vi.fn();
    const fetchFn = vi.fn(async (_path: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.method).toBe("POST");
      expect(init?.body).toBe(JSON.stringify({ code: "PAIR_CODE" }));
      return new Response(JSON.stringify({ bearer: "secret-bearer", expires_at: nowSeconds + 60 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });

    const paired = await bootstrapDashboardAuth({
      fetchFn,
      history: { replaceState } as unknown as History,
      location: { pathname: "/pair", hash: "#PAIR_CODE" } as Location,
    });

    expect(paired).toBe(true);
    expect(replaceState.mock.calls[0]?.[2]).toBe("/pair");
    expect(replaceState.mock.calls.at(-1)?.[2]).toBe("/");
    expect(getDashboardBearer()).toBe("secret-bearer");
    expect(JSON.stringify(replaceState.mock.calls)).not.toContain("secret-bearer");
    expect(document.body.textContent).not.toContain("secret-bearer");
  });

  it("scrubs a failed code and never stores it", async () => {
    const replaceState = vi.fn();
    const fetchFn = vi.fn(async () => new Response("denied", { status: 403 }));
    await expect(
      bootstrapDashboardAuth({
        fetchFn,
        history: { replaceState } as unknown as History,
        location: { pathname: "/pair", hash: "#BAD_CODE" } as Location,
      }),
    ).rejects.toThrow("Pairing failed");
    expect(replaceState).toHaveBeenCalledWith(null, "", "/pair");
    expect(getDashboardBearer()).toBeNull();
  });

  it("does nothing away from the pairing route", async () => {
    const fetchFn = vi.fn();
    expect(
      await bootstrapDashboardAuth({
        fetchFn,
        location: { pathname: "/", hash: "" } as Location,
      }),
    ).toBe(false);
    expect(fetchFn).not.toHaveBeenCalled();
  });
});

describe("clearDashboardBearer", () => {
  it("is idempotent", () => {
    expect(() => clearDashboardBearer()).not.toThrow();
  });
});

describe("fetchDashboardMediaObjectUrl", () => {
  it("fetches protected media with auth and creates an object URL only after success", async () => {
    setDashboardBearer({ bearer: "media-secret", expires_at: nowSeconds + 60 });
    const createObjectURL = vi.fn(() => "blob:protected-media");
    vi.stubGlobal("URL", { ...URL, createObjectURL });
    const fetchFn = vi.fn(async (_path: RequestInfo | URL, init?: RequestInit) => {
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer media-secret");
      return new Response(new Blob(["media"]), { status: 200 });
    });
    await expect(fetchDashboardMediaObjectUrl("/api/media", { fetchFn })).resolves.toBe("blob:protected-media");
    expect(createObjectURL).toHaveBeenCalledTimes(1);
  });

  it("does not create an object URL for an error and clears auth on 401", async () => {
    setDashboardBearer({ bearer: "media-secret", expires_at: nowSeconds + 60 });
    const createObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL });
    const fetchFn = vi.fn(async () => new Response("denied", { status: 401 }));
    await expect(fetchDashboardMediaObjectUrl("/api/media", { fetchFn })).rejects.toThrow("media request failed");
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(getDashboardBearer()).toBeNull();
  });
});

describe("downloadDashboardMedia", () => {
  it("downloads an authenticated blob URL without exposing the bearer", async () => {
    setDashboardBearer({ bearer: "download-secret", expires_at: nowSeconds + 60 });
    const createObjectURL = vi.fn(() => "blob:download");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const fetchFn = vi.fn(async (_path: RequestInfo | URL, init?: RequestInit) => {
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer download-secret");
      return new Response(new Blob(["download"]), { status: 200 });
    });

    await downloadDashboardMedia("/api/export", "session.zip", { fetchFn });
    expect(click).toHaveBeenCalledTimes(1);
    expect(document.querySelector('a[href="blob:download"]')).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:download");
  });
});
