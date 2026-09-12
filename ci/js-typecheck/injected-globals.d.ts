// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

// The injected assets under src/octowright/browser_pool/_assets/ are not valid
// standalone JavaScript: each carries __PLACEHOLDER__ tokens that
// browser_pool/visuals.py replaces with `json.dumps(...)` of a Python value
// immediately before injection. Without these declarations tsc reports 13
// "Cannot find name" errors that are not defects, which would bury the real
// findings -- the reason these files went untyped for as long as they did.
//
// The declared type is the JSON shape of the Python value at the substitution
// site in visuals.py. Keeping them accurate is the point: a placeholder typed
// `any` here buys a green check and no safety at all.

// title_tag.js -- the " (emoji) [label]" suffix, with a leading space.
declare const __SUFFIX__: string;

// badge.js
declare const __TAG__: string;
declare const __COLOR__: string;
declare const __POS__: {
  vertical: string;
  horizontal: string;
  transform?: string;
  h_offset?: string;
  v_offset?: string;
};
declare const __OPACITY__: number;
declare const __DASHBOARD_URL__: string;
declare const __INSTANCE_ID__: string;
declare const __PAIRING_REQUIRED__: boolean;

// macro_pill.js
declare const __ID_TAG__: string;
declare const __ID_COLOR__: string;

// viewport_pill.js -- `inset_w`/`inset_h` are null when the launch could not
// measure the window chrome, which the pill reads as "cannot see the window".
declare const __VIEWPORT_INFO__: {
  mode: string;
  width: number;
  height: number;
  inset_w: number | null;
  inset_h: number | null;
};
declare const __VIEWPORT_TOKEN__: string;

// newtab_shortcut.js
declare const __TARGET__: string;

// Properties each script parks on `window` so the Python side can drive it
// through `expose_binding` / `page.evaluate`. They are octowright's own
// namespace on the page, not anything the DOM declares.
//
// Both payloads and both results are deliberately loose. They cross a
// language boundary into `dict[str, Any]` -- `_viewport_action` in
// browser_pool/pool.py returns three different shapes depending on the
// requested action, and `_STATUS_PUSH_JS`'s payload is assembled per call --
// so a precise hand-written interface here would be fiction that drifts from
// the Python with nothing to keep the two in sync. `any` at a boundary that
// genuinely is dynamic is the honest declaration; the value of this file is
// the checking it enables INSIDE each script.

/** The request `viewport_pill.js` sends; `measured` is omitted by "state". */
interface OctowrightViewportRequest {
  action: string;
  token: string;
  measured?: {
    innerWidth: number;
    innerHeight: number;
    outerWidth: number;
    outerHeight: number;
  };
}

interface Window {
  __octowright_macro_status?: (payload: Record<string, any> | null) => void;
  __octowright_viewport_action?: (
    request: OctowrightViewportRequest,
  ) => Promise<Record<string, any>>;
}
