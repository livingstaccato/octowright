import { describe, expect, it } from "vitest";
import { colorForAction, eventHeadline, formatDateTime, formatTime, relativeSeconds, shortUrl, truncate } from "./format.js";

describe("truncate", () => {
  it("returns short strings unchanged", () => {
    expect(truncate("abc", 10)).toBe("abc");
  });
  it("truncates with ellipsis", () => {
    expect(truncate("abcdefgh", 4)).toBe("abc…");
  });
  it("handles tiny max", () => {
    expect(truncate("abcdef", 1)).toBe("a");
    expect(truncate("abcdef", 0)).toBe("");
  });
});

describe("shortUrl", () => {
  it("strips scheme and trailing slash", () => {
    expect(shortUrl("https://example.com/")).toBe("example.com");
  });
  it("keeps path and query", () => {
    expect(shortUrl("https://example.com/foo?bar=1")).toBe("example.com/foo?bar=1");
  });
  it("returns empty for null", () => {
    expect(shortUrl(null)).toBe("");
    expect(shortUrl(undefined)).toBe("");
  });
  it("falls back to original on invalid url", () => {
    expect(shortUrl("not a url")).toBe("not a url");
  });
  it("truncates long urls", () => {
    expect(shortUrl(`https://example.com/${"a".repeat(100)}`, 20)).toHaveLength(20);
  });
});

describe("formatTime", () => {
  it("formats ISO into HH:MM:SS UTC", () => {
    expect(formatTime("2026-04-24T13:45:09.123Z")).toBe("13:45:09");
  });
  it("returns input on bad input", () => {
    expect(formatTime("garbage")).toBe("garbage");
  });
});

describe("formatDateTime", () => {
  it("formats to YYYY-MM-DD HH:MM:SS", () => {
    expect(formatDateTime("2026-04-24T13:45:09.000Z")).toBe("2026-04-24 13:45:09");
  });
  it("falls back on garbage", () => {
    expect(formatDateTime("xx")).toBe("xx");
  });
});

describe("colorForAction", () => {
  it.each([
    ["click", "click"],
    ["dblclick", "click"],
    ["fill", "fill"],
    ["press_key", "fill"],
    ["navigate", "navigate"],
    ["goto", "navigate"],
    ["expect_url", "expect"],
    ["expect_text", "expect"],
    ["error", "error"],
    ["something_else", "default"],
  ] as const)("maps %s to %s", (input, want) => {
    expect(colorForAction(input)).toBe(want);
  });
});

describe("eventHeadline", () => {
  it("prefers selector", () => {
    expect(eventHeadline({ ts: "x", action: "click", selector: "#foo", text: "ignored" })).toBe("#foo");
  });
  it("falls back to url", () => {
    expect(eventHeadline({ ts: "x", action: "navigate", url: "https://example.com" })).toBe("https://example.com");
  });
  it("returns empty when nothing matches", () => {
    expect(eventHeadline({ ts: "x", action: "noop" })).toBe("");
  });
  it("truncates", () => {
    expect(eventHeadline({ ts: "x", action: "fill", value: "x".repeat(100) }, 10)).toHaveLength(10);
  });
});

describe("relativeSeconds", () => {
  it("computes positive offset", () => {
    expect(relativeSeconds("2026-04-24T13:45:10.000Z", "2026-04-24T13:45:00.000Z")).toBe(10);
  });
  it("clamps negative to 0", () => {
    expect(relativeSeconds("2026-04-24T13:45:00.000Z", "2026-04-24T13:45:10.000Z")).toBe(0);
  });
  it("handles bad dates", () => {
    expect(relativeSeconds("nope", "nope")).toBe(0);
  });
});
