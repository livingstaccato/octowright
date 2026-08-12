import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setDashboardBearer } from "./dashboard-auth.js";
import { openDashboardEventStream } from "./dashboard-events.js";

async function flush(): Promise<void> {
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
}

beforeEach(() => {
  sessionStorage.clear();
  setDashboardBearer({ bearer: "stream-secret", expires_at: Date.now() / 1000 + 60 });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("openDashboardEventStream", () => {
  it("authenticates fetch and parses SSE across byte and line boundaries", async () => {
    const bytes = new TextEncoder().encode('event: invalidate\ndata: {"scope":"sessions","note":"💥"}\n\n');
    const emojiStart = bytes.indexOf(0xf0);
    const chunks = [bytes.slice(0, 7), bytes.slice(7, emojiStart + 2), bytes.slice(emojiStart + 2)];
    const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
    const fetchFn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ input, init });
      return new Response(
        new ReadableStream({
          start(controller) {
            for (const chunk of chunks) controller.enqueue(chunk);
            controller.close();
          },
        }),
        { status: 200, headers: { "content-type": "text/event-stream" } },
      );
    });
    const onInvalidate = vi.fn();
    const handle = openDashboardEventStream({ fetchFn, onInvalidate });
    await flush();
    handle.close();

    expect(calls[0]?.input).toBe("/api/dashboard/events");
    expect(new Headers(calls[0]?.init?.headers).get("Authorization")).toBe("Bearer stream-secret");
    expect(onInvalidate).toHaveBeenCalledWith('{"scope":"sessions","note":"💥"}');
  });

  it("aborts the active request on close", async () => {
    let signal: AbortSignal | undefined;
    const fetchFn = vi.fn(
      async (_input: RequestInfo | URL, init?: RequestInit) =>
        await new Promise<Response>((_resolve, reject) => {
          signal = init?.signal ?? undefined;
          signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    );
    const handle = openDashboardEventStream({ fetchFn, onInvalidate: () => {} });
    await flush();
    handle.close();
    await flush();
    expect(signal?.aborted).toBe(true);
  });

  it("marks a clean server-side stream close unhealthy before reconnecting", async () => {
    const callbacks: Array<() => void> = [];
    const onError = vi.fn();
    const fetchFn = vi.fn(
      async () =>
        new Response(
          new ReadableStream({
            start(controller) {
              controller.close();
            },
          }),
          { status: 200 },
        ),
    );
    const handle = openDashboardEventStream({
      fetchFn,
      onInvalidate: () => {},
      onError,
      setTimeoutFn: (callback) => {
        callbacks.push(callback);
        return callbacks.length as unknown as ReturnType<typeof setTimeout>;
      },
    });
    await flush();
    expect(onError).toHaveBeenCalledOnce();
    expect(callbacks).toHaveLength(1);
    handle.close();
  });

  it("stops permanently on 401 and asks the page to re-pair", async () => {
    const callbacks: Array<() => void> = [];
    const listener = vi.fn();
    window.addEventListener("octowright:dashboard-auth-required", listener);
    const handle = openDashboardEventStream({
      fetchFn: vi.fn(async () => new Response("denied", { status: 401 })),
      onInvalidate: () => {},
      onError: vi.fn(),
      setTimeoutFn: (callback) => {
        callbacks.push(callback);
        return callbacks.length as unknown as ReturnType<typeof setTimeout>;
      },
    });
    await flush();
    expect(listener).toHaveBeenCalledOnce();
    expect(callbacks).toHaveLength(0);
    handle.close();
    window.removeEventListener("octowright:dashboard-auth-required", listener);
  });

  it("reconnects with exponential delay capped at the configured maximum", async () => {
    const delays: number[] = [];
    const callbacks: Array<() => void> = [];
    const fetchFn = vi.fn(async () => {
      throw new Error("offline");
    });
    const handle = openDashboardEventStream({
      fetchFn,
      onInvalidate: () => {},
      reconnectBaseMs: 100,
      reconnectMaxMs: 250,
      setTimeoutFn: (callback, delay) => {
        callbacks.push(callback);
        delays.push(delay);
        return delays.length as unknown as ReturnType<typeof setTimeout>;
      },
    });
    await flush();
    callbacks.shift()?.();
    await flush();
    callbacks.shift()?.();
    await flush();
    callbacks.shift()?.();
    await flush();
    handle.close();
    expect(delays).toEqual([100, 200, 250, 250]);
  });
});
