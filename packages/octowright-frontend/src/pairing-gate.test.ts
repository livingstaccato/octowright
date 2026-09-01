import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderPairingGate } from "./pairing-gate.js";

let root: HTMLElement;

beforeEach(() => {
  document.body.innerHTML = "";
  root = document.createElement("div");
  document.body.append(root);
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("renderPairingGate", () => {
  it("replaces the pane instead of decorating whatever failed to load", () => {
    root.innerHTML = '<p class="stale">No live sessions.</p>';
    renderPairingGate(root, { reason: "never-paired" });
    expect(root.querySelector(".stale")).toBeNull();
    expect(root.querySelectorAll('[data-testid="pairing-gate"]')).toHaveLength(1);
  });

  it("names every way back in, with the commands verbatim", () => {
    renderPairingGate(root, { reason: "never-paired" });
    const commands = [...root.querySelectorAll(".pairing-gate__command")].map((el) => el.textContent);
    expect(commands).toEqual(["octowright dashboard --open", "octowright dashboard"]);
    // The agent route has no command to copy, so it is prose only.
    expect(root.textContent).toContain("octowright_dashboard_url");
  });

  it("says why the gate exists rather than only that it exists", () => {
    renderPairingGate(root, { reason: "never-paired" });
    expect(root.textContent).toContain("typed input");
    expect(root.textContent).toContain("OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING=0");
  });

  it("explains a link that never carried a code without implying breakage", () => {
    renderPairingGate(root, { reason: "never-paired" });
    const gate = root.querySelector('[data-testid="pairing-gate"]');
    expect(gate?.getAttribute("data-reason")).toBe("never-paired");
    expect(gate?.textContent).toContain("Nothing is broken");
  });

  it("names both causes for a refused bearer, because they are one 401 from here", () => {
    renderPairingGate(root, { reason: "rejected" });
    const gate = root.querySelector('[data-testid="pairing-gate"]');
    expect(gate?.getAttribute("data-reason")).toBe("rejected");
    expect(gate?.textContent).toContain("expire");
    expect(gate?.textContent).toContain("restarted");
  });

  it("omits the session note unless a session was being opened", () => {
    renderPairingGate(root, { reason: "never-paired" });
    expect(root.querySelector('[data-testid="pairing-gate-session"]')).toBeNull();
  });

  it("tells a locked-out reader their recording still exists", () => {
    renderPairingGate(root, { reason: "rejected", sessionId: "deadbeef00" });
    const note = root.querySelector('[data-testid="pairing-gate-session"]');
    expect(note?.textContent).toContain("deadbeef00");
    expect(note?.textContent).toContain("browser_recording_path");
  });

  it("drops the shell's refresh promise, which the gate has just made false", () => {
    const hint = document.createElement("span");
    hint.className = "topbar__hint";
    hint.textContent = "refreshes every 5s";
    document.body.prepend(hint);

    renderPairingGate(root, { reason: "never-paired" });
    expect(document.querySelector(".topbar__hint")).toBeNull();
  });

  it("copies a command and says so", async () => {
    vi.useFakeTimers();
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });

    renderPairingGate(root, { reason: "never-paired" });
    const button = root.querySelector<HTMLButtonElement>('[data-testid="pairing-gate-copy"]');
    button?.click();
    await vi.advanceTimersByTimeAsync(0);

    expect(writeText).toHaveBeenCalledWith("octowright dashboard --open");
    expect(button?.textContent).toBe("copied");
    await vi.advanceTimersByTimeAsync(2000);
    expect(button?.textContent).toBe("copy");
    vi.unstubAllGlobals();
  });

  it("tells the reader to select the text when there is no clipboard API", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("navigator", {});

    renderPairingGate(root, { reason: "never-paired" });
    const button = root.querySelector<HTMLButtonElement>('[data-testid="pairing-gate-copy"]');
    button?.click();
    await vi.advanceTimersByTimeAsync(0);

    // Silently doing nothing is the one unacceptable outcome on a page whose
    // whole job is to unstick someone.
    expect(button?.textContent).toBe("select it");
    vi.unstubAllGlobals();
  });
});
