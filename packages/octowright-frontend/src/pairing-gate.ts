// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

/**
 * The blocking panel shown when the dashboard has no usable pairing bearer.
 *
 * It REPLACES the page rather than decorating it. The state it replaces was
 * actively false in three directions at once: every panel printed an
 * affirmative empty state ("No live sessions.") while sessions were running,
 * a banner promised "Retrying automatically." after `authRequired` had already
 * called `stopPolling()`, and the one accurate message was a snackbar that
 * auto-hid after 3.5s. Nothing on the settled page said the word "pairing" or
 * named a command, so the dashboard read as broken rather than as locked.
 *
 * The badge overlay injected into every launched browser links here, and its
 * links can never carry a code: a pairing code is single-use with a 60s TTL,
 * and the init script that would have to hold one runs in the page, where
 * every site the browser visits could read it. So arriving here without a
 * bearer is the normal case, not an edge case, and the page has to be able to
 * explain itself to someone who does not know what pairing is.
 */

const CLIPBOARD_FEEDBACK_MS = 1400;

/**
 * Why there is no usable bearer.
 *
 * Only two states are distinguishable from the browser, and claiming a third
 * would be a guess: once the leader refuses a bearer we held, "it aged out"
 * and "the daemon restarted and forgot every pairing" are the same 401. The
 * copy names both causes rather than picking one.
 */
export type PairingGateReason = "never-paired" | "rejected";

export interface PairingGateContext {
  reason: PairingGateReason;
  /** Set on the session page so the gate can name what the user was opening. */
  sessionId?: string;
}

interface Route {
  command?: string;
  lead: string;
  detail: string;
}

const HEADLINE: Record<PairingGateReason, string> = {
  "never-paired": "This dashboard link doesn't carry a pairing code",
  rejected: "This dashboard pairing is no longer valid",
};

const EXPLANATION: Record<PairingGateReason, string> = {
  "never-paired":
    "Nothing is broken — the dashboard just hasn't been let in yet. Links that reach it from " +
    "elsewhere (the corner badge inside a launched browser, a bookmark, a typed address) carry no " +
    "credential, so every one of them lands here.",
  rejected:
    "Either the pairing sat unused long enough to expire, or the octowright daemon restarted — " +
    "pairings live in the leader's memory and none of them survive a restart. Both look identical " +
    "from here, so this page won't guess which.",
};

const WHY =
  "The dashboard can read every recording on this machine — typed input, visited URLs, console " +
  "output — and drive live browsers. That is why it isn't simply open to anything that can reach " +
  "the port, and why it wants a one-time code first.";

const ROUTES: Route[] = [
  {
    command: "octowright dashboard --open",
    lead: "Run this in a terminal",
    detail: "Opens a paired tab straight away. Easiest if you have a shell open.",
  },
  {
    command: "octowright dashboard",
    lead: "Or run this and open the URL it prints",
    detail: "The code in that URL is single-use and expires 60 seconds after it is minted.",
  },
  {
    lead: 'Or ask your agent: "open the octowright dashboard"',
    detail: "It calls octowright_dashboard_url and hands back a link that stays good for 10 minutes.",
  },
];

const OPT_OUT =
  "On a single-user machine and don't want this? Start the daemon with " +
  "OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING=0. Any local process can then read your recordings and " +
  "drive your browsers.";

function copyButton(command: string): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn pairing-gate__copy";
  button.textContent = "copy";
  button.setAttribute("data-testid", "pairing-gate-copy");
  button.addEventListener("click", () => {
    const restore = (label: string): void => {
      button.textContent = label;
      setTimeout(() => {
        button.textContent = "copy";
      }, CLIPBOARD_FEEDBACK_MS);
    };
    // A page served over plain http:// on a non-localhost host has no
    // clipboard API at all, so the failure path has to say something the
    // reader can act on rather than silently doing nothing.
    void (navigator.clipboard?.writeText(command) ?? Promise.reject(new Error("no clipboard")))
      .then(() => restore("copied"))
      .catch(() => restore("select it"));
  });
  return button;
}

function routeItem(route: Route): HTMLLIElement {
  const item = document.createElement("li");
  item.className = "pairing-gate__route";

  const lead = document.createElement("p");
  lead.className = "pairing-gate__route-lead";
  lead.textContent = route.lead;
  item.append(lead);

  if (route.command) {
    const row = document.createElement("div");
    row.className = "pairing-gate__command-row";
    const command = document.createElement("code");
    command.className = "pairing-gate__command";
    command.textContent = route.command;
    row.append(command, copyButton(route.command));
    item.append(row);
  }

  const detail = document.createElement("p");
  detail.className = "pairing-gate__route-detail";
  detail.textContent = route.detail;
  item.append(detail);
  return item;
}

function sessionNote(sessionId: string): HTMLParagraphElement {
  const note = document.createElement("p");
  note.className = "pairing-gate__session";
  note.setAttribute("data-testid", "pairing-gate-session");
  // The recording is on disk either way. Saying so stops "I can't open the
  // dashboard" from reading as "the recording is gone".
  note.textContent =
    `You were opening session ${sessionId}. Its recording is still on disk — ` +
    "browser_recording_path returns the path, with or without a paired dashboard.";
  return note;
}

/** Replace ``root`` with the blocking pairing panel. */
export function renderPairingGate(root: HTMLElement, ctx: PairingGateContext): void {
  root.innerHTML = "";
  // Static shell copy that the gate has just made false: polling is stopped.
  // Leaving it up repeats, in the chrome, the same lie the panels told.
  document.querySelector(".topbar__hint")?.remove();

  const panel = document.createElement("section");
  panel.className = "pairing-gate";
  panel.setAttribute("data-testid", "pairing-gate");
  panel.setAttribute("data-reason", ctx.reason);
  panel.setAttribute("role", "alert");

  const heading = document.createElement("h2");
  heading.className = "pairing-gate__headline";
  heading.textContent = HEADLINE[ctx.reason];

  const explanation = document.createElement("p");
  explanation.className = "pairing-gate__lede";
  explanation.textContent = EXPLANATION[ctx.reason];

  const why = document.createElement("p");
  why.className = "pairing-gate__why";
  why.textContent = WHY;

  panel.append(heading, explanation, why);
  if (ctx.sessionId) panel.append(sessionNote(ctx.sessionId));

  const routesHeading = document.createElement("h3");
  routesHeading.className = "pairing-gate__routes-heading";
  routesHeading.textContent = "Get back in";

  const routes = document.createElement("ol");
  routes.className = "pairing-gate__routes";
  for (const route of ROUTES) routes.append(routeItem(route));

  const optOut = document.createElement("p");
  optOut.className = "pairing-gate__opt-out";
  optOut.textContent = OPT_OUT;

  panel.append(routesHeading, routes, optOut);
  root.append(panel);
}
