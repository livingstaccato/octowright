import { dashboardAuthHeaders, handleDashboardUnauthorized } from "./dashboard-auth.js";

export interface DashboardEventStreamHandle {
  close(): void;
}

export interface DashboardEventStreamOptions {
  url?: string;
  onInvalidate: (data: string | undefined) => void;
  onOpen?: () => void;
  onError?: (error: unknown) => void;
  fetchFn?: typeof fetch;
  reconnectBaseMs?: number;
  reconnectMaxMs?: number;
  setTimeoutFn?: (callback: () => void, delay: number) => ReturnType<typeof setTimeout>;
  clearTimeoutFn?: (timer: ReturnType<typeof setTimeout>) => void;
}

interface SseEvent {
  name: string;
  data: string[];
}

async function readSse(
  stream: ReadableStream<Uint8Array>,
  onInvalidate: (data: string | undefined) => void,
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let event: SseEvent = { name: "message", data: [] };

  const processLine = (rawLine: string): void => {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (line === "") {
      if (event.name === "invalidate") {
        onInvalidate(event.data.length > 0 ? event.data.join("\n") : undefined);
      }
      event = { name: "message", data: [] };
      return;
    }
    if (line.startsWith(":")) return;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    let value = separator < 0 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event.name = value;
    if (field === "data") event.data.push(value);
  };

  const processBuffer = (flush: boolean): void => {
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      processLine(buffer.slice(0, newline));
      buffer = buffer.slice(newline + 1);
      newline = buffer.indexOf("\n");
    }
    if (flush && buffer) {
      processLine(buffer);
      buffer = "";
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      processBuffer(false);
    }
    buffer += decoder.decode();
    processBuffer(true);
    processLine("");
  } finally {
    reader.releaseLock();
  }
}

export function openDashboardEventStream(options: DashboardEventStreamOptions): DashboardEventStreamHandle {
  const fetchFn = options.fetchFn ?? fetch;
  const setTimer = options.setTimeoutFn ?? ((callback, delay) => setTimeout(callback, delay));
  const clearTimer = options.clearTimeoutFn ?? ((timer) => clearTimeout(timer));
  const baseDelay = Math.max(1, options.reconnectBaseMs ?? 500);
  const maxDelay = Math.max(baseDelay, options.reconnectMaxMs ?? 10_000);
  let closed = false;
  let failures = 0;
  let controller: AbortController | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  const scheduleReconnect = (): void => {
    if (closed) return;
    const delay = Math.min(maxDelay, baseDelay * 2 ** failures);
    failures += 1;
    reconnectTimer = setTimer(() => {
      reconnectTimer = null;
      void connect();
    }, delay);
  };

  const connect = async (): Promise<void> => {
    if (closed) return;
    controller = new AbortController();
    try {
      const response = await fetchFn(options.url ?? "/api/dashboard/events", {
        method: "GET",
        headers: dashboardAuthHeaders({ Accept: "text/event-stream" }),
        signal: controller.signal,
      });
      if (response.status === 401) handleDashboardUnauthorized();
      if (!response.ok || !response.body) {
        throw new Error(`dashboard event stream failed (${response.status})`);
      }
      failures = 0;
      options.onOpen?.();
      await readSse(response.body, options.onInvalidate);
      if (!closed) {
        options.onError?.(new Error("dashboard event stream closed"));
        scheduleReconnect();
      }
    } catch (error) {
      if (closed || (error instanceof DOMException && error.name === "AbortError")) return;
      options.onError?.(error);
      scheduleReconnect();
    }
  };

  void connect();
  return {
    close() {
      if (closed) return;
      closed = true;
      controller?.abort();
      if (reconnectTimer !== null) clearTimer(reconnectTimer);
      reconnectTimer = null;
    },
  };
}
