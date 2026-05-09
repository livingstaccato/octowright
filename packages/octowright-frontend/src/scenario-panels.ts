// Live and saved scenario list panels. Live scenarios show participant
// chips that link to each session's debugger; saved scenarios show a
// "▶ Start" button that delegates back to the dashboard's action handler.

import { formatDateTime } from "./format.js";
import type { LiveScenario, SavedScenario } from "./types.js";

export function renderScenarioList(scenarios: LiveScenario[]): HTMLElement {
  if (scenarios.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No live scenarios.";
    return empty;
  }
  const ul = document.createElement("ul");
  ul.className = "scenario-list";
  for (const scenario of scenarios) {
    const li = document.createElement("li");
    li.className = "scenario-list__item";
    li.setAttribute("data-scenario-id", scenario.scenario_id);
    const title = document.createElement("div");
    title.className = "scenario-list__title";
    title.textContent = scenario.name;
    const chips = document.createElement("div");
    chips.className = "chips";
    for (const part of scenario.participants) {
      const chip = document.createElement("a");
      chip.className = `chip chip--${part.kind}`;
      chip.href = `/sessions/${encodeURIComponent(part.instance_id)}`;
      chip.textContent = `${part.role}: ${part.persona}`;
      chips.append(chip);
    }
    li.append(title, chips);
    ul.append(li);
  }
  return ul;
}

export function renderSavedScenarios(
  scenarios: SavedScenario[],
  onStart: (name: string) => void,
): HTMLElement {
  if (scenarios.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No saved scenarios on disk.";
    return empty;
  }
  const ul = document.createElement("ul");
  ul.className = "scenario-list";
  for (const s of [...scenarios].sort((a, b) => b.mtime - a.mtime)) {
    const li = document.createElement("li");
    li.className = "scenario-list__item saved-scenario";
    li.setAttribute("data-scenario-name", s.name);

    const main = document.createElement("div");
    main.className = "saved-scenario__main";
    const title = document.createElement("div");
    title.className = "scenario-list__title";
    title.textContent = s.name;
    const meta = document.createElement("div");
    meta.className = "saved-scenario__meta";
    meta.textContent = `${s.form} · ${formatDateTime(new Date(s.mtime * 1000).toISOString())}`;
    main.append(title, meta);

    const btn = document.createElement("button");
    btn.className = "btn btn--primary";
    btn.setAttribute("aria-label", `Start scenario ${s.name}`);
    btn.textContent = "▶ Start";
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      onStart(s.name);
    });

    li.append(main, btn);
    ul.append(li);
  }
  return ul;
}
