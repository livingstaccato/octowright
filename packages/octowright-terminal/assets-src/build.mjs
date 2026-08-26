// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

// Bundles src/renderer.ts into the single self-contained module the Python
// package ships and the dashboard loads at runtime via `import()`. There is
// no bundler on the serving path -- http/routes/plugin_assets.py serves this
// file verbatim off disk -- so the output must carry xterm and its two
// addons inlined rather than as bare `import "@xterm/xterm"` specifiers
// nothing resolves in a browser.
//
// Invoked via `npm run build`, which runs `tsc --noEmit` first (see
// package.json): esbuild itself never type-checks, it only
// transpiles+bundles, so a contract-breaking signature change here would
// otherwise pass silently through this script alone.

import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const outfile = path.join(here, "..", "src", "octowright_terminal", "assets", "renderer.js");

await build({
  entryPoints: [path.join(here, "src", "renderer.ts")],
  outfile,
  bundle: true,
  format: "esm",
  target: "es2020",
  platform: "browser",
  // Inline xterm's own stylesheet as a JS string (see src/css.d.ts) instead
  // of emitting a separate .css asset: nothing on the serving side would
  // load a sibling stylesheet for a plugin module, so the renderer injects
  // it itself at mount time.
  loader: { ".css": "text" },
  minify: true,
  legalComments: "none",
  sourcemap: false,
});

console.log(`built ${path.relative(process.cwd(), outfile)}`);
