import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getPersonaDetail: vi.fn(),
  updatePersonaYaml: vi.fn(),
}));

vi.mock("./api.js", () => apiMocks);

const { openPersonaEditor } = await import("./persona-editor.js");

async function flushPromises(): Promise<void> {
  for (let i = 0; i < 5; i++) await Promise.resolve();
}

beforeEach(() => {
  document.body.innerHTML = "";
  vi.clearAllMocks();
  apiMocks.getPersonaDetail.mockResolvedValue({
    name: "qa",
    yaml: "name: qa\n",
    path: "/profiles/qa",
    disk_bytes: 0,
    engine_bytes: {},
  });
});

describe("openPersonaEditor backdrop behavior", () => {
  it("closes when the backdrop itself is clicked", async () => {
    openPersonaEditor("qa");
    await flushPromises();

    const backdrop = document.querySelector<HTMLElement>(".modal-backdrop");
    expect(backdrop).not.toBeNull();
    backdrop?.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(document.querySelector(".modal-backdrop")).toBeNull();
  });

  it("does not close when an inner modal element is clicked", async () => {
    openPersonaEditor("qa");
    await flushPromises();

    document.querySelector<HTMLElement>(".modal")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(document.querySelector(".modal-backdrop")).not.toBeNull();
  });
});
