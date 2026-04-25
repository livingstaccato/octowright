// Build-artifact integration test.
//
// This is the test that would have caught today's production bug: the prior
// `tsc + cp` pipeline emitted ESM with bare specifier imports
// (`import ... from "@provide-io/telemetry"`) that worked in vitest (because
// vite's resolver inlines `@provide-io/telemetry`) but failed in real browsers
// with `Module name '@provide-io/telemetry' does not resolve to a valid URL`.
//
// We shell out to `vite build`, then assert against the emitted artifacts:
//   * No bare specifier IMPORT STATEMENTS survive in the bundled JS (string
//     literals containing `@provide-io/telemetry` are fine — the library uses
//     its own name for service identity).
//   * Pino (the actual log backend) is bundled in — proves the dependency
//     graph was traversed, not stubbed away by the OTEL plugin.
//   * The HTML files exist at the outDir root with rewritten <script src=…>
//     pointing at the bundled JS, NOT at the original `dashboard.js`/`session.js`
//     names that the tsc pipeline produced.
//
// If this test fails the message is loud on purpose: production has been
// silently broken for hours when this kind of bug ships.

import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeAll, describe, expect, it } from "vitest";

const PKG_ROOT = resolve(__dirname, "..");
const OUT_DIR = resolve(PKG_ROOT, "../../src/octowright/server/frontend");

const FAIL_MSG_BARE_IMPORTS =
  "production bundle has unresolved bare imports — Vite build is broken or " +
  "misconfigured. Browsers cannot resolve `import ... from \"@provide-io/...\"` " +
  "without a bundler. Re-run `npm run build` and inspect vite.config.ts.";

// Match real ES import/export statements with a bare specifier
// (`import x from "@scope/pkg"`, `export * from "pkg"`, dynamic `import("pkg")`).
// Crafted to avoid matching string literals like `"@provide-io/telemetry"` that
// just happen to appear inside template strings or object values.
const BARE_IMPORT_RE =
  /(?:^|[\s;{])(?:import\s*(?:[\w*${},\s]+\s*from\s*)?|export\s+[\w*${},\s]+\s*from\s*|import\s*\(\s*)["'](@?[a-z][\w./@-]*)["']/gi;

describe("vite build artifacts", () => {
  beforeAll(() => {
    execSync("npx vite build", {
      cwd: PKG_ROOT,
      stdio: "inherit",
      // 60s ceiling — first-run cold builds on this repo finish in <1s, but
      // CI machines and cold node_modules can be slower.
      timeout: 60_000,
    });
  }, 90_000);

  it("emits index.html and session.html at outDir root", () => {
    expect(existsSync(resolve(OUT_DIR, "index.html"))).toBe(true);
    expect(existsSync(resolve(OUT_DIR, "session.html"))).toBe(true);
  });

  it("emits at least one .js file per HTML entry, plus a CSS file", () => {
    const indexHtml = readFileSync(resolve(OUT_DIR, "index.html"), "utf8");
    const sessionHtml = readFileSync(resolve(OUT_DIR, "session.html"), "utf8");
    // Vite rewrites <script src=…> from the source TS to the bundled name.
    // Source HTML had `../src/dashboard.ts` — if that survives, the rewrite
    // never happened and the browser will 404.
    expect(indexHtml).not.toMatch(/\.\.\/src\//);
    expect(sessionHtml).not.toMatch(/\.\.\/src\//);
    // Match flat or hashed names: `/index.js` or `/assets/index-abc123.js`.
    expect(indexHtml).toMatch(/<script[^>]+src="[^"]+\.js"/);
    expect(sessionHtml).toMatch(/<script[^>]+src="[^"]+\.js"/);
    // Stylesheet must still be linked.
    expect(indexHtml).toMatch(/<link[^>]+rel="stylesheet"[^>]+href="[^"]+\.css"/);
  });

  it("contains zero bare-specifier import statements in any bundled JS", () => {
    const bundled = bundledJsFiles();
    expect(bundled.length).toBeGreaterThan(0);

    const offenders: { file: string; specifier: string; snippet: string }[] = [];
    for (const file of bundled) {
      const src = readFileSync(file, "utf8");
      // Reset regex state between files.
      BARE_IMPORT_RE.lastIndex = 0;
      for (const m of src.matchAll(BARE_IMPORT_RE)) {
        const spec = m[1];
        // Bare specifiers are anything that doesn't start with `./`, `../`, or `/`.
        // The regex above already restricts to those, but be defensive.
        if (!spec || spec.startsWith(".") || spec.startsWith("/")) continue;
        const at = m.index ?? 0;
        offenders.push({
          file,
          specifier: spec,
          snippet: src.slice(Math.max(0, at - 20), at + 80),
        });
      }
    }

    if (offenders.length > 0) {
      const report = offenders
        .map((o) => `  - ${o.specifier} in ${o.file}\n      …${o.snippet}…`)
        .join("\n");
      throw new Error(`${FAIL_MSG_BARE_IMPORTS}\nOffenders:\n${report}`);
    }
  });

  it("inlines pino (the actual telemetry backend), not just stubs it away", () => {
    // If the OTEL stub plugin over-reaches and stubs the whole telemetry chain,
    // pino's own code disappears too and runtime logging is silently broken.
    // Pino's source includes the literal string `pino` in many places — match
    // any of a few stable identifiers.
    const bundled = bundledJsFiles().map((f) => readFileSync(f, "utf8")).join("\n");
    const hasPinoMarker =
      /pino/.test(bundled) || /asJson/.test(bundled) || /__pinoLogs/.test(bundled);
    expect(
      hasPinoMarker,
      "no pino-shaped marker found in bundle — telemetry dep was stubbed away?",
    ).toBe(true);
  });
});

function bundledJsFiles(): string[] {
  // Walk one level deep — flat output puts everything at OUT_DIR root, but
  // hashed output may use OUT_DIR/assets/. Cheap recursive scan.
  const out: string[] = [];
  const stack: string[] = [OUT_DIR];
  while (stack.length > 0) {
    const dir = stack.pop();
    if (!dir) continue;
    const { readdirSync, statSync } = require("node:fs");
    for (const name of readdirSync(dir)) {
      const full = resolve(dir, name);
      const st = statSync(full);
      if (st.isDirectory()) stack.push(full);
      else if (name.endsWith(".js")) out.push(full);
    }
  }
  return out;
}
