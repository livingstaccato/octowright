import type { DemoListResponse } from "./types.js";

function listText(items: string[], emptyLabel: string): string {
  return items.length > 0 ? items.join(", ") : emptyLabel;
}

function appendChipRow(parent: HTMLElement, items: string[], prefix: string): void {
  if (items.length === 0) {
    return;
  }
  const chips = document.createElement("div");
  chips.className = "chips";
  for (const item of items) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = `${prefix}: ${item}`;
    chips.append(chip);
  }
  parent.append(chips);
}

function metadataLine(label: string, value: string): HTMLParagraphElement {
  const line = document.createElement("p");
  line.textContent = `${label}: ${value}`;
  return line;
}

function renderDemoItem(demo: DemoListResponse["heroes"][number]): HTMLElement {
  const item = document.createElement("li");
  item.className = "scenario-list__item";
  item.setAttribute("data-demo-id", demo.id);

  const title = document.createElement("div");
  title.className = "scenario-list__title";
  title.textContent = demo.title;

  const summary = document.createElement("p");
  summary.textContent = demo.summary ?? "No summary provided.";

  item.append(
    title,
    summary,
    metadataLine("Engines", listText(demo.engines, "none")),
    metadataLine("Roles", listText(demo.roles, "none")),
    metadataLine("Scenarios", listText(demo.scenarios, "none")),
    metadataLine(
      "Replay artifacts",
      `${demo.artifacts.replay.existing_count}/${demo.artifacts.replay.declared_count}`,
    ),
    metadataLine(
      "Video artifacts",
      `${demo.artifacts.video.existing_count}/${demo.artifacts.video.declared_count}`,
    ),
  );
  appendChipRow(item, demo.tags, "tag");
  appendChipRow(item, demo.audiences, "audience");

  if (demo.regen_command) {
    const regen = document.createElement("code");
    regen.textContent = demo.regen_command;
    item.append(regen);
  }

  return item;
}

function renderDemoSection(
  title: string,
  testId: string,
  demos: DemoListResponse["heroes"],
  emptyMessage: string,
): HTMLElement {
  const section = document.createElement("section");
  section.setAttribute("data-testid", testId);

  const heading = document.createElement("h3");
  heading.textContent = title;
  section.append(heading);

  if (demos.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = emptyMessage;
    section.append(empty);
    return section;
  }

  const grid = document.createElement("ul");
  grid.className = "scenario-list";
  for (const demo of demos) {
    grid.append(renderDemoItem(demo));
  }
  section.append(grid);
  return section;
}

export function renderDemoGallery(root: HTMLElement, demos: DemoListResponse): void {
  root.innerHTML = "";
  root.append(
    renderDemoSection("Hero demos", "demo-heroes", demos.heroes, "No hero demos available."),
    renderDemoSection(
      "Supporting demos",
      "demo-supporting",
      demos.supporting,
      "No supporting demos available.",
    ),
  );
}
