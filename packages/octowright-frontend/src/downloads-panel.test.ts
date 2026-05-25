import { beforeEach, describe, expect, it } from "vitest";
import { renderDownloadsPanel } from "./downloads-panel.js";
import type { DownloadEntry } from "./types.js";

const SAMPLE: DownloadEntry[] = [
  {
    url: "https://octowright.com/file1.pdf",
    suggested_filename: "file1.pdf",
    path: "/tmp/dl/file1.pdf",
    timestamp: "2026-04-24T12:00:00.000Z",
    path_exists: true,
  },
  {
    url: "https://octowright.com/super-long-path/that/is/definitely-going-to-exceed-the-truncation-limit/file2.zip",
    suggested_filename: "file2.zip",
    path: "/tmp/dl/file2.zip",
    timestamp: "2026-04-24T12:01:00.000Z",
    path_exists: false,
  },
];

let container: HTMLDivElement;
beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
});

describe("renderDownloadsPanel", () => {
  it("renders empty placeholder when none", () => {
    renderDownloadsPanel(container, []);
    const empty = container.querySelector('[data-testid="downloads-empty"]');
    expect(empty?.textContent).toBe("No downloads");
  });

  it("renders one row per download", () => {
    renderDownloadsPanel(container, SAMPLE);
    const rows = container.querySelectorAll('[data-testid="downloads-row"]');
    expect(rows.length).toBe(2);
  });

  it("includes filename, url, timestamp cells", () => {
    renderDownloadsPanel(container, SAMPLE);
    const rows = container.querySelectorAll('[data-testid="downloads-row"]');
    expect(rows[0]?.querySelector(".downloads-panel__filename")?.textContent).toBe("file1.pdf");
    expect(rows[0]?.querySelector(".downloads-panel__url")?.textContent).toContain("octowright.com");
  });

  it("truncates long urls but keeps full url in title attr", () => {
    renderDownloadsPanel(container, SAMPLE);
    const rows = container.querySelectorAll('[data-testid="downloads-row"]');
    const urlSpan = rows[1]?.querySelector(".downloads-panel__url span") as HTMLElement | null;
    expect(urlSpan).not.toBeNull();
    expect(urlSpan?.textContent?.length).toBeLessThanOrEqual(60);
    expect(urlSpan?.title).toBe(SAMPLE[1]!.url);
  });

  it("shows missing badge when path_exists is false", () => {
    renderDownloadsPanel(container, SAMPLE);
    const rows = container.querySelectorAll('[data-testid="downloads-row"]');
    expect(rows[0]?.querySelector('[data-testid="download-missing-badge"]')).toBeNull();
    expect(rows[1]?.querySelector('[data-testid="download-missing-badge"]')).not.toBeNull();
    expect(rows[1]?.querySelector('[data-testid="download-missing-badge"]')?.textContent).toBe("missing");
  });

  it("omits size column when no entries provide size", () => {
    renderDownloadsPanel(container, SAMPLE);
    const ths = container.querySelectorAll("thead th");
    const labels = Array.from(ths, (th) => th.textContent);
    expect(labels).not.toContain("size");
  });

  it("includes size column when at least one entry has size_bytes", () => {
    renderDownloadsPanel(container, [{ ...SAMPLE[0]!, size_bytes: 2048 }]);
    const ths = container.querySelectorAll("thead th");
    const labels = Array.from(ths, (th) => th.textContent);
    expect(labels).toContain("size");
    const sizeCell = container.querySelector(".downloads-panel__size");
    expect(sizeCell?.textContent).toContain("2.0 KB");
  });

  it("falls back from suggested filename to basename and unknown", () => {
    renderDownloadsPanel(container, [
      { ...SAMPLE[0]!, suggested_filename: "", path: "C:\\tmp\\fallback.bin" },
      { ...SAMPLE[1]!, suggested_filename: "", path: "" },
      { ...SAMPLE[1]!, suggested_filename: "", path: "/" },
    ]);
    const filenames = Array.from(container.querySelectorAll(".downloads-panel__filename"), (el) => el.textContent);
    expect(filenames).toEqual(["fallback.bin", "(unknown)", "/"]);
  });

  it("formats byte, megabyte, gigabyte, missing, and invalid sizes", () => {
    renderDownloadsPanel(container, [
      { ...SAMPLE[0]!, size_bytes: 512 },
      { ...SAMPLE[0]!, suggested_filename: "mb.bin", size_bytes: 2 * 1024 * 1024 },
      { ...SAMPLE[0]!, suggested_filename: "gb.bin", size_bytes: 2 * 1024 * 1024 * 1024 },
      { ...SAMPLE[0]!, suggested_filename: "missing.bin" },
      { ...SAMPLE[0]!, suggested_filename: "bad.bin", size_bytes: Number.POSITIVE_INFINITY },
      { ...SAMPLE[0]!, suggested_filename: "negative.bin", size_bytes: -1 },
    ]);

    const sizes = Array.from(container.querySelectorAll(".downloads-panel__size"), (el) => el.textContent);
    expect(sizes).toEqual(["512 B", "2.0 MB", "2.00 GB", "", "", ""]);
  });
});
