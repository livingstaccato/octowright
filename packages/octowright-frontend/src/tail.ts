import { getLogger, wsConnectsCounter, wsMessagesCounter } from "./telemetry.js";
import type { RecordingEvent } from "./types.js";

const log = getLogger("octowright.frontend.tail");

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
  log.info({ event: "ws_connecting", url });
  ws.addEventListener("open", () => {
    wsConnectsCounter.add(1, { kind: "tail" });
    log.info({ event: "ws_connect", url });
  });
  ws.addEventListener("message", (raw) => {
    const data = (raw as MessageEvent).data;
    if (typeof data !== "string") return;
    try {
      const parsed = JSON.parse(data) as TailMessage;
      if (Array.isArray(parsed.events) && typeof parsed.cursor === "number") {
        wsMessagesCounter.add(1, { kind: "tail" });
        log.debug({
          event: "ws_message",
          batch_size: parsed.events.length,
          cursor: parsed.cursor,
          complete: parsed.complete ?? false,
        });
        opts.onMessage(parsed);
      } else {
        log.warn({ event: "ws_message_invalid", reason: "missing_fields" });
      }
    } catch (err) {
      log.warn({ event: "ws_message_invalid", reason: "parse_error", error: String(err) });
    }
  });
  ws.addEventListener("error", (e) => {
    log.warn({ event: "ws_error", url });
    if (opts.onError) opts.onError(e as Event);
  });
  ws.addEventListener("close", (e) => {
    const ce = e as CloseEvent;
    log.info({ event: "ws_close", url, code: ce.code, reason: ce.reason, was_clean: ce.wasClean });
    if (opts.onClose) opts.onClose(ce);
  });
  return {
    close() {
      try {
        ws.close();
        log.debug({ event: "ws_close_requested", url });
      } catch (err) {
        log.debug({ event: "ws_close_failed", url, error: String(err) });
      }
    },
  };
}
