import { beforeEach, describe, expect, it } from "vitest";
import { renderDemoGallery } from "./demo-gallery.js";
import type { DemoListResponse } from "./types.js";

let root: HTMLDivElement;

beforeEach(() => {
  document.body.innerHTML = "";
  root = document.createElement("div");
  document.body.append(root);
});

const demos: DemoListResponse = {
  heroes: [
    {
      id: "hero-checkout",
      title: "Checkout Hero",
      summary: "Walk through a complete checkout flow.",
      hero: true,
      audiences: ["sales", "product"],
      tags: ["checkout", "hero"],
      engines: ["chromium", "webkit"],
      roles: ["shopper", "monitor"],
      scenarios: ["checkout-two-party"],
      regen_command: "uv run octowright demo regen hero-checkout",
      tutorial_export: "docs/tutorials/hero-checkout.md",
      artifacts: {
        replay: {
          declared_count: 2,
          existing_count: 1,
          declared_paths: ["replays/checkout.jsonl", "replays/assertions.jsonl"],
          existing_paths: ["replays/checkout.jsonl"],
        },
        video: {
          declared_count: 1,
          existing_count: 1,
          declared_paths: ["videos/checkout.mp4"],
          existing_paths: ["videos/checkout.mp4"],
        },
      },
    },
  ],
  supporting: [
    {
      id: "support-admin",
      title: "Admin Support",
      summary: "Covers the supporting admin workflow.",
      hero: false,
      audiences: ["ops"],
      tags: ["admin"],
      engines: ["firefox"],
      roles: ["admin"],
      scenarios: ["admin-backoffice"],
      regen_command: null,
      tutorial_export: null,
      artifacts: {
        replay: {
          declared_count: 0,
          existing_count: 0,
          declared_paths: [],
          existing_paths: [],
        },
        video: {
          declared_count: 1,
          existing_count: 0,
          declared_paths: ["videos/admin.mp4"],
          existing_paths: [],
        },
      },
    },
  ],
};

describe("renderDemoGallery", () => {
  it("renders hero and supporting sections with artifact counts and regen command", () => {
    renderDemoGallery(root, demos);

    const heroes = root.querySelector('[data-testid="demo-heroes"]');
    const supporting = root.querySelector('[data-testid="demo-supporting"]');

    expect(heroes?.querySelectorAll("li.scenario-list__item").length).toBe(1);
    expect(supporting?.querySelectorAll("li.scenario-list__item").length).toBe(1);
    expect(heroes?.textContent).toContain("Checkout Hero");
    expect(heroes?.textContent).toContain("Walk through a complete checkout flow.");
    expect(heroes?.textContent).toContain("Replay artifacts: 1/2");
    expect(heroes?.textContent).toContain("Video artifacts: 1/1");
    expect(heroes?.querySelector("code")?.textContent).toBe("uv run octowright demo regen hero-checkout");
    expect(supporting?.textContent).toContain("Admin Support");
    expect(supporting?.textContent).toContain("Covers the supporting admin workflow.");
    expect(supporting?.textContent).toContain("Replay artifacts: 0/0");
    expect(supporting?.textContent).toContain("Video artifacts: 0/1");
  });

  it("renders empty states when no demos exist in a section", () => {
    renderDemoGallery(root, { heroes: [], supporting: [] });

    expect(root.querySelector('[data-testid="demo-heroes"] .empty')?.textContent).toContain(
      "No hero demos available.",
    );
    expect(root.querySelector('[data-testid="demo-supporting"] .empty')?.textContent).toContain(
      "No supporting demos available.",
    );
  });
});
