// Persona panel: a grid of cards, each with a name, engines, last-used
// timestamp, on-disk size, and an edit button that opens the YAML editor.
// The on-disk-size figures are populated lazily by `loadPersonaSizes` from
// dashboard.ts and consumed via the `sizesProvider` accessor passed in.

import { formatBytes, formatDateTime } from "./format.js";
import type { PersonaSummary } from "./types.js";

export interface PersonaGridDeps {
  /** Returns the persona-name -> bytes map and whether it has loaded yet. */
  sizesProvider: () => { sizes: Record<string, number | null>; loaded: boolean };
  onEdit: (name: string) => void;
}

export function renderPersonaGrid(personas: PersonaSummary[], deps: PersonaGridDeps): HTMLElement {
  if (personas.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No personas saved.";
    return empty;
  }
  const { sizes, loaded } = deps.sizesProvider();
  const grid = document.createElement("div");
  grid.className = "persona-grid";
  for (const p of personas) {
    const card = document.createElement("div");
    card.className = "persona-card";
    card.setAttribute("data-persona-name", p.name);

    const name = document.createElement("div");
    name.className = "persona-card__name";
    name.textContent = p.display_name ?? p.name;

    const engines = document.createElement("div");
    engines.className = "persona-card__engines";
    engines.textContent = p.engines.join(", ") || "no engines";

    const last = document.createElement("div");
    last.className = "persona-card__last";
    last.textContent = `last used ${formatDateTime(p.last_used)}`;

    const sizeEl = document.createElement("div");
    sizeEl.className = "persona-card__size";
    if (loaded) {
      const bytes = sizes[p.name];
      sizeEl.textContent = bytes != null ? formatBytes(bytes) : "—";
    } else {
      sizeEl.textContent = "…";
    }

    const actions = document.createElement("div");
    actions.className = "persona-card__actions";

    const editBtn = document.createElement("button");
    editBtn.className = "icon-btn";
    editBtn.setAttribute("aria-label", `Edit persona ${p.name}`);
    editBtn.setAttribute("title", "Edit YAML");
    editBtn.textContent = "✎";
    editBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      deps.onEdit(p.name);
    });
    actions.append(editBtn);

    card.append(name, engines, last, sizeEl, actions);
    grid.append(card);
  }
  return grid;
}
