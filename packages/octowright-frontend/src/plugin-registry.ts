// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

/**
 * What renders a kind, and whether this dashboard may use it.
 *
 * The version gate lives here rather than in the boot path so the boot path
 * has exactly one decision: render the plugin's module, or render the fallback
 * with the reason this module already produced.
 *
 * RENDERER_API_VERSION is the version THIS SPA implements. It is deliberately
 * separate from the backend's plugin_api_version, which the loader checks:
 * collapsing them would make this path unreachable, because a mismatched
 * plugin would be refused at load and never reach /api/plugins -- and a plugin
 * whose UI is a version behind should not be refused wholesale.
 */

import type { FallbackReason } from "./session-fallback.js";
import { getLogger } from "./telemetry.js";

const log = getLogger("octowright.frontend.plugin-registry");

/** Bump when the StreamContext/StreamHandle contract changes shape. */
export const RENDERER_API_VERSION = 1;

export interface PluginFrontend {
  moduleUrl: string;
  rendererApiVersion: number;
  displayName: string;
  layout: "browser" | "stream";
}

export async function loadPluginRegistry(
  fetchImpl: typeof fetch = fetch,
): Promise<Map<string, PluginFrontend>> {
  // A dashboard that cannot reach /api/plugins still has to render browser
  // sessions, so this degrades to "no plugin renderers" rather than failing
  // the page.
  try {
    const resp = await fetchImpl("/api/plugins");
    if (!resp.ok) {
      log.warn({ event: "plugin_registry_not_ok", status: resp.status });
      return new Map();
    }
    const body = (await resp.json()) as Record<string, PluginFrontend>;
    return new Map(Object.entries(body));
  } catch (err) {
    log.warn({ event: "plugin_registry_fetch_failed", error: String(err) });
    return new Map();
  }
}

export function resolveRenderer(
  registry: Map<string, PluginFrontend>,
  kind: string,
): { moduleUrl: string; layout: "browser" | "stream" } | FallbackReason {
  const entry = registry.get(kind);
  if (!entry) {
    return { code: "no-frontend", detail: "" };
  }
  if (entry.rendererApiVersion !== RENDERER_API_VERSION) {
    return {
      code: "version-mismatch",
      detail: `plugin targets renderer API v${entry.rendererApiVersion}, dashboard implements v${RENDERER_API_VERSION}`,
    };
  }
  return { moduleUrl: entry.moduleUrl, layout: entry.layout };
}
