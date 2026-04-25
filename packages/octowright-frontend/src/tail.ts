import type { RecordingEvent } from "./types.js";

export interface TailMessage {
  events: RecordingEvent[];
  cursor: number;
  complete?: boolean;
}

export interface TailHandle {
  close(): void;
}

export interface TailOptions {
  onMessage: (message: TailMessage) => void;
  onError?: (event: Event) => void;
  onClose?: (event: CloseEvent) => void;
  /** Inject a WebSocket constructor — useful in tests. */
  webSocketCtor?: typeof WebSocket;
}

export function openTail(url: string, opts: TailOptions): TailHandle {
  const Ctor = opts.webSocketCtor ?? WebSocket;
  const ws = new Ctor(url);
  ws.addEventListener("message", (raw) => {
    const data = (raw as MessageEvent).data;
    if (typeof data !== "string") return;
    try {
      const parsed = JSON.parse(data) as TailMessage;
      if (Array.isArray(parsed.events) && typeof parsed.cursor === "number") {
        opts.onMessage(parsed);
      }
    } catch {
      // ignore malformed frames
    }
  });
  if (opts.onError) {
    ws.addEventListener("error", opts.onError);
  }
  if (opts.onClose) {
    ws.addEventListener("close", (e) => opts.onClose?.(e as CloseEvent));
  }
  return {
    close() {
      try {
        ws.close();
      } catch {
        // already closed
      }
    },
  };
}
