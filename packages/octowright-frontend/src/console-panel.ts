import type { ConsoleLevel, ConsoleMessage } from "./types.js";

const LEVEL_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "all", label: "All" },
  { value: "log", label: "Log" },
  { value: "info", label: "Info" },
  { value: "warn", label: "Warn" },
  { value: "error", label: "Error" },
];

export function badgeClassForLevel(level: ConsoleLevel): string {
  const l = (level ?? "log").toLowerCase();
  if (l === "error") return "console-badge--error";
  if (l === "warn" || l === "warning") return "console-badge--warn";
  if (l === "info") return "console-badge--info";
  if (l === "debug") return "console-badge--debug";
  return "console-badge--log";
}

export function filterMessages(messages: ConsoleMessage[], level: string): ConsoleMessage[] {
  if (!level || level === "all") return messages;
  const target = level.toLowerCase();
  return messages.filter((m) => (m.level ?? "log").toLowerCase() === target);
}

export interface ConsolePanelOptions {
  initialLevel?: string;
}

export function renderConsolePanel(
  container: HTMLElement,
  messages: ConsoleMessage[],
  opts: ConsolePanelOptions = {},
): void {
  container.innerHTML = "";
  container.classList.add("console-panel");
  container.setAttribute("data-testid", "console-panel");

  const toolbar = document.createElement("div");
  toolbar.className = "console-panel__toolbar";

  const select = document.createElement("select");
  select.className = "console-panel__filter";
  select.setAttribute("data-testid", "console-filter");
  select.setAttribute("aria-label", "Filter console messages by level");
  for (const opt of LEVEL_OPTIONS) {
    const optionEl = document.createElement("option");
    optionEl.value = opt.value;
    optionEl.textContent = opt.label;
    select.append(optionEl);
  }
  const initialLevel = opts.initialLevel ?? "all";
  select.value = initialLevel;

  const count = document.createElement("span");
  count.className = "console-panel__count";
  count.setAttribute("data-testid", "console-count");

  toolbar.append(select, count);

  const list = document.createElement("ol");
  list.className = "console-panel__list";
  list.setAttribute("data-testid", "console-list");

  const empty = document.createElement("p");
  empty.className = "empty";
  empty.textContent = "No console messages";
  empty.setAttribute("data-testid", "console-empty");

  container.append(toolbar, list, empty);

  const apply = (level: string): void => {
    const filtered = filterMessages(messages, level);
    list.innerHTML = "";
    if (filtered.length === 0) {
      list.style.display = "none";
      empty.style.display = "";
    } else {
      list.style.display = "";
      empty.style.display = "none";
      for (const msg of filtered) {
        list.append(renderRow(msg));
      }
    }
    count.textContent = `${filtered.length} of ${messages.length}`;
  };

  select.addEventListener("change", () => {
    apply(select.value);
  });

  apply(initialLevel);
}

function renderRow(msg: ConsoleMessage): HTMLLIElement {
  const li = document.createElement("li");
  li.className = "console-panel__row";
  li.setAttribute("data-level", (msg.level ?? "log").toLowerCase());

  const badge = document.createElement("span");
  badge.className = `console-badge ${badgeClassForLevel(msg.level)}`;
  badge.textContent = (msg.level ?? "log").toLowerCase();
  li.append(badge);

  if (msg.page_index !== null && msg.page_index !== undefined) {
    const tab = document.createElement("span");
    tab.className = "console-panel__tab";
    tab.textContent = `[tab ${msg.page_index}]`;
    li.append(tab);
  }

  const text = document.createElement("span");
  text.className = "console-panel__text";
  text.textContent = msg.text;
  li.append(text);

  return li;
}
