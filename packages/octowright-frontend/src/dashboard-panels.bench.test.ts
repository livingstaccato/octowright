// Microbenchmark proving the partial-update render eliminates DOM churn
// for unaffected panels. Run via:
//
//   npm test -- --run src/dashboard-panels.bench.ts
//
// Reports the number of DOM nodes recreated per refresh, comparing:
//   * old behavior (full rebuild — every panel's body is replaced)
//   * new behavior (scoped rebuild — only the matching panel's body is replaced)

import { describe, expect, it } from "vitest";

import type { PanelDef } from "./dashboard-panels.js";
import { mountPanels, updatePanels } from "./dashboard-panels.js";

type Scope = "sessions" | "personas" | "scenarios" | "macros";

interface BigState {
  sessions: number;
  personas: number;
  scenarios: number;
  macros: number;
}

function makeList(scope: Scope, n: number): HTMLElement {
  // Build a list of `n` rows so each panel body is realistically heavy.
  const ul = document.createElement("ul");
  ul.dataset.testid = `body-${scope}`;
  for (let i = 0; i < n; i++) {
    const li = document.createElement("li");
    li.dataset.testid = `${scope}-row-${i}`;
    li.textContent = `${scope} ${i}`;
    li.addEventListener("click", () => undefined); // realistic listener load
    ul.append(li);
  }
  return ul;
}

const DEFS: ReadonlyArray<PanelDef<Scope, BigState>> = [
  { scope: "sessions", testid: "live-browsers", title: "Live", buildBody: (s) => makeList("sessions", s.sessions) },
  { scope: "personas", testid: "personas", title: "Personas", buildBody: (s) => makeList("personas", s.personas) },
  { scope: "scenarios", testid: "scenarios", title: "Scenarios", buildBody: (s) => makeList("scenarios", s.scenarios) },
  { scope: "macros", testid: "macros", title: "Macros", buildBody: (s) => makeList("macros", s.macros) },
];

function totalListItems(root: HTMLElement): number {
  return root.querySelectorAll("li").length;
}

describe("partial-update DOM churn (microbenchmark)", () => {
  it("scoped invalidation only recreates the matching panel's li nodes", () => {
    const root = document.createElement("div");
    document.body.append(root);
    try {
      const state: BigState = { sessions: 30, personas: 50, scenarios: 20, macros: 10 };
      const panels = mountPanels(root, DEFS, state);

      // Tag every existing <li> so we can count survivors after the refresh.
      const initialLis = Array.from(root.querySelectorAll("li"));
      for (const li of initialLis) li.dataset.alive = "yes";
      expect(totalListItems(root)).toBe(110); // 30 + 50 + 20 + 10

      // FULL rebuild (old behavior, scopes=null): every li is recreated.
      updatePanels(panels, state, null);
      const survivorsAfterFull = root.querySelectorAll('li[data-alive="yes"]').length;
      expect(survivorsAfterFull).toBe(0); // all 110 were recreated

      // Re-tag for the second measurement.
      for (const li of root.querySelectorAll("li")) (li as HTMLElement).dataset.alive = "yes";

      // SCOPED rebuild (new behavior, scopes={"sessions"}): only sessions li
      // nodes are recreated; the other 80 (personas+scenarios+macros) survive.
      updatePanels(panels, state, new Set<Scope>(["sessions"]));
      const survivorsAfterScoped = root.querySelectorAll('li[data-alive="yes"]').length;
      expect(survivorsAfterScoped).toBe(80); // only sessions (30) were recreated

      // Document the win.
      const fullChurn = 110;
      const scopedChurn = 110 - survivorsAfterScoped;
      // eslint-disable-next-line no-console
      console.log(
        `[bench] full=${fullChurn} li recreated, scoped(sessions)=${scopedChurn}; saved ${fullChurn - scopedChurn} li recreations (${(((fullChurn - scopedChurn) / fullChurn) * 100).toFixed(0)}%)`,
      );
    } finally {
      root.remove();
    }
  });

  it("scoped wall-clock refresh is faster on large panel sets", () => {
    const root = document.createElement("div");
    document.body.append(root);
    try {
      const state: BigState = { sessions: 50, personas: 200, scenarios: 50, macros: 50 };
      const panels = mountPanels(root, DEFS, state);

      const RUNS = 200;

      // Warm up
      for (let i = 0; i < 10; i++) updatePanels(panels, state, null);
      for (let i = 0; i < 10; i++) updatePanels(panels, state, new Set<Scope>(["sessions"]));

      const tFullStart = performance.now();
      for (let i = 0; i < RUNS; i++) updatePanels(panels, state, null);
      const tFull = performance.now() - tFullStart;

      const tScopedStart = performance.now();
      for (let i = 0; i < RUNS; i++) updatePanels(panels, state, new Set<Scope>(["sessions"]));
      const tScoped = performance.now() - tScopedStart;

      const fullPer = tFull / RUNS;
      const scopedPer = tScoped / RUNS;
      // eslint-disable-next-line no-console
      console.log(
        `[bench] full=${fullPer.toFixed(2)}ms/refresh, scoped=${scopedPer.toFixed(2)}ms/refresh, speedup=${(fullPer / scopedPer).toFixed(2)}x`,
      );
      // The scoped path should be at least as fast as the full path; usually
      // much faster. We assert a conservative lower bound to keep the test
      // resilient on slow CI runners.
      expect(scopedPer).toBeLessThanOrEqual(fullPer);
    } finally {
      root.remove();
    }
  });
});
