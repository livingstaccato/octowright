import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { PanelDef } from "./dashboard-panels.js";
import { mountPanels, updatePanels } from "./dashboard-panels.js";

type Scope = "alpha" | "beta" | "gamma";

interface FakeState {
  alphaCount: number;
  betaCount: number;
  gammaCount: number;
}

function makeBody(scope: Scope, n: number): HTMLElement {
  const el = document.createElement("div");
  el.dataset.testid = `body-${scope}`;
  el.dataset.count = String(n);
  return el;
}

const DEFS: ReadonlyArray<PanelDef<Scope, FakeState>> = [
  { scope: "alpha", testid: "alpha", title: "Alpha", buildBody: (s) => makeBody("alpha", s.alphaCount) },
  { scope: "beta", testid: "beta", title: "Beta", buildBody: (s) => makeBody("beta", s.betaCount) },
  {
    scope: "gamma",
    testid: "gamma",
    title: "Gamma",
    collapsible: true,
    defaultOpen: true,
    buildBody: (s) => makeBody("gamma", s.gammaCount),
  },
];

describe("mountPanels / updatePanels", () => {
  let root: HTMLDivElement;

  beforeEach(() => {
    root = document.createElement("div");
    document.body.append(root);
  });

  afterEach(() => {
    root.remove();
  });

  it("mounts every panel and uses the right wrapper element per collapsible flag", () => {
    const panels = mountPanels(root, DEFS, { alphaCount: 1, betaCount: 2, gammaCount: 3 });

    expect(panels).toHaveLength(3);
    expect(root.querySelector('[data-testid="panel-alpha"]')?.tagName).toBe("SECTION");
    expect(root.querySelector('[data-testid="panel-beta"]')?.tagName).toBe("SECTION");
    const gamma = root.querySelector<HTMLDetailsElement>('[data-testid="panel-gamma"]');
    expect(gamma?.tagName).toBe("DETAILS");
    expect(gamma?.open).toBe(true);
  });

  it("preserves <details> open state across re-mount", () => {
    const panels = mountPanels(root, DEFS, { alphaCount: 0, betaCount: 0, gammaCount: 0 });
    const gamma1 = panels.find((p) => p.testid === "gamma");
    const gammaEl1 = gamma1!.root as HTMLDetailsElement;
    gammaEl1.open = false;

    mountPanels(root, DEFS, { alphaCount: 0, betaCount: 0, gammaCount: 0 });
    const gammaEl2 = root.querySelector<HTMLDetailsElement>('[data-testid="panel-gamma"]');
    expect(gammaEl2?.open).toBe(false); // user's collapsed state survived
  });

  it("updatePanels with a scoped set only re-renders matching panels", () => {
    const panels = mountPanels(root, DEFS, { alphaCount: 1, betaCount: 2, gammaCount: 3 });

    // Capture body element identity before the update.
    const alphaBodyBefore = panels[0].root.children[1];
    const betaBodyBefore = panels[1].root.children[1];
    const gammaBodyBefore = panels[2].root.children[1];

    updatePanels(panels, { alphaCount: 99, betaCount: 99, gammaCount: 99 }, new Set<Scope>(["beta"]));

    // alpha + gamma bodies are the same node (untouched).
    expect(panels[0].root.children[1]).toBe(alphaBodyBefore);
    expect(panels[2].root.children[1]).toBe(gammaBodyBefore);
    // beta body was replaced.
    expect(panels[1].root.children[1]).not.toBe(betaBodyBefore);
    expect((panels[1].root.children[1] as HTMLElement).dataset.count).toBe("99");
    // alpha + gamma still show old counts since their buildBody wasn't called.
    expect((panels[0].root.children[1] as HTMLElement).dataset.count).toBe("1");
    expect((panels[2].root.children[1] as HTMLElement).dataset.count).toBe("3");
  });

  it("updatePanels with null scopes refreshes every panel", () => {
    const panels = mountPanels(root, DEFS, { alphaCount: 1, betaCount: 2, gammaCount: 3 });
    const before = panels.map((p) => p.root.children[1]);

    updatePanels(panels, { alphaCount: 10, betaCount: 20, gammaCount: 30 }, null);

    for (let i = 0; i < panels.length; i++) {
      expect(panels[i].root.children[1]).not.toBe(before[i]);
    }
    expect((panels[0].root.children[1] as HTMLElement).dataset.count).toBe("10");
    expect((panels[1].root.children[1] as HTMLElement).dataset.count).toBe("20");
    expect((panels[2].root.children[1] as HTMLElement).dataset.count).toBe("30");
  });

  it("preserves listeners on the panel wrapper across scoped updates of OTHER panels", () => {
    const panels = mountPanels(root, DEFS, { alphaCount: 0, betaCount: 0, gammaCount: 0 });
    let alphaClicks = 0;
    panels[0].root.addEventListener("click", () => {
      alphaClicks += 1;
    });

    // Scoped update for "beta" should not touch alpha — listener survives.
    updatePanels(panels, { alphaCount: 99, betaCount: 99, gammaCount: 99 }, new Set<Scope>(["beta"]));
    panels[0].root.dispatchEvent(new Event("click", { bubbles: true }));
    expect(alphaClicks).toBe(1);

    // Even a full refresh keeps wrapper-level listeners (only bodies are replaced).
    updatePanels(panels, { alphaCount: 0, betaCount: 0, gammaCount: 0 }, null);
    panels[0].root.dispatchEvent(new Event("click", { bubbles: true }));
    expect(alphaClicks).toBe(2);
  });

  it("calls buildBody only for matching scopes", () => {
    const calls: Scope[] = [];
    const tracking: ReadonlyArray<PanelDef<Scope, FakeState>> = DEFS.map((d) => ({
      ...d,
      buildBody: (s: FakeState) => {
        calls.push(d.scope);
        return d.buildBody(s);
      },
    }));
    const state = { alphaCount: 0, betaCount: 0, gammaCount: 0 };
    const panels = mountPanels(root, tracking, state);
    expect(calls).toEqual(["alpha", "beta", "gamma"]); // initial mount

    calls.length = 0;
    updatePanels(panels, state, new Set<Scope>(["alpha", "gamma"]));
    expect(calls).toEqual(["alpha", "gamma"]);

    calls.length = 0;
    updatePanels(panels, state, new Set<Scope>(["beta"]));
    expect(calls).toEqual(["beta"]);

    calls.length = 0;
    updatePanels(panels, state, new Set<Scope>()); // empty set: nothing
    expect(calls).toEqual([]);
  });
});
