// Generic scope-keyed panel mount + partial-update primitives for the
// dashboard. Each "panel" is a <section> (or <details>) wrapping a heading
// plus a body element. On a refresh we replace only the body of panels
// whose scope is in the changed-scope set, leaving wrappers, headings,
// listeners, scroll positions, and <details> open state untouched.
//
// This module is deliberately decoupled from dashboard.ts: it knows
// nothing about DashboardState or DashboardScope. dashboard.ts assembles
// the registry; this file just operates on it.

export interface PanelDef<Scope, State> {
  /** Which scope identifier from the SSE invalidation set drives this panel. */
  scope: Scope;
  /** Stable test id; rendered as ``data-testid="panel-{testid}"``. */
  testid: string;
  /** Visible heading text (h2 or summary). */
  title: string;
  /** When true, panel renders as <details> with the heading as <summary>. */
  collapsible?: boolean;
  /** Default open state for collapsible panels (only used on first mount). */
  defaultOpen?: boolean;
  /** Build the panel's body element from the current state. */
  buildBody: (state: State) => HTMLElement;
}

export interface PanelInstance<Scope, State> {
  scope: Scope;
  testid: string;
  /** The <section> or <details> element. Stable across updates. */
  root: HTMLElement;
  buildBody: (state: State) => HTMLElement;
}

function panelSection(
  title: string,
  testid: string,
  body: HTMLElement,
  opts: { collapsible?: boolean; open?: boolean } = {},
): HTMLElement {
  const wrapper = opts.collapsible ? document.createElement("details") : document.createElement("section");
  wrapper.className = `panel panel--${testid}`;
  wrapper.setAttribute("data-testid", `panel-${testid}`);
  if (opts.collapsible && wrapper instanceof HTMLDetailsElement) {
    wrapper.open = opts.open ?? false;
  }
  const heading = opts.collapsible ? document.createElement("summary") : document.createElement("h2");
  heading.className = "panel__title";
  heading.textContent = title;
  wrapper.append(heading, body);
  return wrapper;
}

function buildPanelInstance<Scope, State>(
  def: PanelDef<Scope, State>,
  state: State,
  openPanels: Map<string, boolean>,
): PanelInstance<Scope, State> {
  const wrapper = panelSection(def.title, def.testid, def.buildBody(state), {
    collapsible: def.collapsible,
    open: openPanels.get(`panel-${def.testid}`) ?? def.defaultOpen ?? false,
  });
  return { scope: def.scope, testid: def.testid, root: wrapper, buildBody: def.buildBody };
}

/**
 * Build the full panel tree under ``root`` and return the registry the
 * caller can later hand back to ``updatePanels`` for partial refreshes.
 *
 * Open <details> state from any prior mount under the same root is
 * preserved (queried before the root is cleared).
 */
export function mountPanels<Scope, State>(
  root: HTMLElement,
  defs: ReadonlyArray<PanelDef<Scope, State>>,
  state: State,
): Array<PanelInstance<Scope, State>> {
  const openPanels = new Map(
    Array.from(root.querySelectorAll<HTMLDetailsElement>("details[data-testid]")).map((el) => [
      el.dataset.testid ?? "",
      el.open,
    ]),
  );
  root.innerHTML = "";
  const panels = defs.map((def) => buildPanelInstance(def, state, openPanels));
  for (const p of panels) root.append(p.root);
  return panels;
}

/**
 * Replace the body of each panel whose scope is in ``scopes`` (or every
 * panel if ``scopes`` is null). Wrappers, headings, listeners on those,
 * and <details> open state survive the update — only the body subtree is
 * rebuilt.
 */
export function updatePanels<Scope, State>(
  panels: ReadonlyArray<PanelInstance<Scope, State>>,
  state: State,
  scopes: ReadonlySet<Scope> | null,
): void {
  for (const panel of panels) {
    if (scopes !== null && !scopes.has(panel.scope)) continue;
    const newBody = panel.buildBody(state);
    const oldBody = panel.root.children[1];
    if (oldBody !== undefined) {
      panel.root.replaceChild(newBody, oldBody);
    } else {
      panel.root.append(newBody);
    }
  }
}
