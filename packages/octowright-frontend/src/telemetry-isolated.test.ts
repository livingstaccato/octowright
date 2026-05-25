import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("telemetry isolated globals", () => {
  it("initializes without a browser window", async () => {
    vi.resetModules();
    vi.stubGlobal("window", undefined);
    const telemetry = await import("./telemetry.js");

    expect(telemetry.detectEnvironment()).toBe("development");
    expect(() => telemetry.initTelemetry()).not.toThrow();
  });

  it("initializes production log settings with a browser-like window", async () => {
    vi.resetModules();
    const addEventListener = vi.fn();
    vi.stubGlobal("window", {
      addEventListener,
      location: { hostname: "octowright.example" },
    });
    const telemetry = await import("./telemetry.js");

    expect(telemetry.detectEnvironment()).toBe("production");
    expect(() => telemetry.initTelemetry({ pageName: "prod" })).not.toThrow();
    expect(addEventListener).toHaveBeenCalledWith("error", expect.any(Function));
    expect(addEventListener).toHaveBeenCalledWith("unhandledrejection", expect.any(Function));
  });
});
