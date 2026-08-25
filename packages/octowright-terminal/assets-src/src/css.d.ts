// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

// build.mjs configures esbuild's `.css` loader as `text`, which turns a CSS
// import into a plain string module (the file's contents as the default
// export) rather than a stylesheet side effect -- the renderer injects that
// string into a <style> tag itself, see the module docstring in renderer.ts
// for why. This ambient declaration is what makes that import shape legal to
// tsc, which otherwise has no idea what a `.css` specifier resolves to.
declare module "*.css" {
  const content: string;
  export default content;
}
