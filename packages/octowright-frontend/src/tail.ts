import { dashboardWebSocketProtocols, handleDashboardStreamAuthClose } from "./dashboard-auth.js";
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

export type TailStatus = "connected" | "reconnecting" | "ended";

/**
 * Keep a live tail going across a dropped socket.
 *
 * Without it a tail that closed for any reason but auth (a 1006 from a
 * network blip, a 1011 from the server) froze the timeline silently while the
 * page still claimed to be refreshing. With it the tail reconnects from the
 * last cursor it was given, backing off exponentially, and stops for good on
 * `complete`, an auth close, an unknown session, or `close()`.
 */
export interface TailReconnectOptions {
  /** The tail URL that resumes after `cursor`. */
  urlFor: (cursor: number) => string;
  /** The cursor the first URL resumes from, used until a frame reports one. */
  initialCursor: number;
  onStatus?: (status: TailStatus) => void;
  /**
   * The session closed while the tail was down: the server refuses to tail a
   * closed session (1003), so whatever it wrote after `cursor` has to come
   * from `/events` instead.
   */
  onSessionClosed?: (cursor: number) => void;
  baseDelayMs?: number;
  maxDelayMs?: number;
  setTimeoutFn?: (callback: () => void, delayMs: number) => unknown;
  clearTimeoutFn?: (handle: unknown) => void;
}

export interface TailOptions {
  onMessage: (message: TailMessage) => void;
  onError?: (event: Event) => void;
  onClose?: (event: CloseEvent) => void;
  /** Inject a WebSocket constructor — useful in tests. */
  webSocketCtor?: typeof WebSocket;
  /** Reconnect after a non-terminal close; omitted, a close ends the tail. */
  reconnect?: TailReconnectOptions;
}

const TAIL_RECONNECT_BASE_MS = 1000;
const TAIL_RECONNECT_MAX_MS = 15000;
/** The server's answer to a tail of a session that is no longer live. */
const CLOSE_CODE_SESSION_CLOSED = 1003;
/** Policy violation: the session is unknown (or the bearer was refused). */
const CLOSE_CODE_POLICY = 1008;

export function openTail(url: string, opts: TailOptions): TailHandle {
  const reconnect = opts.reconnect;
  const setTimer = reconnect?.setTimeoutFn ?? ((cb: () => void, ms: number) => window.setTimeout(cb, ms));
  const clearTimer =
    reconnect?.clearTimeoutFn ?? ((h: unknown) => window.clearTimeout(h as ReturnType<typeof window.setTimeout>));
  let cursor = reconnect?.initialCursor ?? 0;
  let attempt = 0;
  let stopped = false;
  let timer: unknown = null;
  let ws: WebSocket;

  const setStatus = (status: TailStatus): void => {
    reconnect?.onStatus?.(status);
  };
  const end = (): void => {
    stopped = true;
    setStatus("ended");
  };

  const connect = (target: string): void => {
    ws = openSocket(target, opts.webSocketCtor, {
      onOpen: () => setStatus("connected"),
      onMessage: (parsed) => {
        cursor = parsed.cursor;
        attempt = 0;
        // Before the handler, so a throwing handler cannot leave a finished
        // session scheduled for reconnects.
        if (parsed.complete && reconnect) end();
        opts.onMessage(parsed);
      },
      onError: opts.onError,
      onClose: (ce, wasAuth) => {
        if (opts.onClose) opts.onClose(ce);
        if (!reconnect || stopped) return;
        if (wasAuth) {
          end();
          return;
        }
        if (ce.code === CLOSE_CODE_SESSION_CLOSED) {
          end();
          reconnect.onSessionClosed?.(cursor);
          return;
        }
        if (ce.code === CLOSE_CODE_POLICY) {
          end();
          return;
        }
        const base = reconnect.baseDelayMs ?? TAIL_RECONNECT_BASE_MS;
        const delay = Math.min(base * 2 ** attempt, reconnect.maxDelayMs ?? TAIL_RECONNECT_MAX_MS);
        attempt += 1;
        setStatus("reconnecting");
        log.info({ event: "ws_reconnect_scheduled", url: target, delay_ms: delay, attempt, cursor });
        timer = setTimer(() => {
          timer = null;
          if (!stopped) connect(reconnect.urlFor(cursor));
        }, delay);
      },
    });
  };
  connect(url);

  return {
    close() {
      stopped = true;
      if (timer !== null) {
        clearTimer(timer);
        timer = null;
      }
      try {
        ws.close();
        log.debug({ event: "ws_close_requested", url });
      } catch (err) {
        log.debug({ event: "ws_close_failed", url, error: String(err) });
      }
    },
  };
}

interface SocketCallbacks {
  onOpen: () => void;
  onMessage: (message: TailMessage) => void;
  onError: ((event: Event) => void) | undefined;
  onClose: (event: CloseEvent, wasAuthClose: boolean) => void;
}

function openSocket(url: string, ctor: typeof WebSocket | undefined, cb: SocketCallbacks): WebSocket {
  // Resolved per socket: a test (or a page) may stub the global after load.
  const Ctor = ctor ?? WebSocket;
  const protocols = dashboardWebSocketProtocols();
  const ws = protocols.length > 0 ? new Ctor(url, protocols) : new Ctor(url);
  log.info({ event: "ws_connecting", url });
  ws.addEventListener("open", () => {
    wsConnectsCounter.add(1, { kind: "tail" });
    log.info({ event: "ws_connect", url });
    cb.onOpen();
  });
  ws.addEventListener("message", (raw) => {
    const data = (raw as MessageEvent).data;
    if (typeof data !== "string") return;
    let parsed: TailMessage;
    try {
      parsed = JSON.parse(data) as TailMessage;
    } catch (err) {
      log.warn({ event: "ws_message_invalid", reason: "parse_error", error: String(err) });
      return;
    }
    if (Array.isArray(parsed.events) && typeof parsed.cursor === "number") {
      wsMessagesCounter.add(1, { kind: "tail" });
      log.debug({
        event: "ws_message",
        batch_size: parsed.events.length,
        cursor: parsed.cursor,
        complete: parsed.complete ?? false,
      });
      try {
        cb.onMessage(parsed);
      } catch (err) {
        log.warn({ event: "ws_message_handler_failed", error: String(err) });
      }
    } else {
      log.warn({ event: "ws_message_invalid", reason: "missing_fields" });
    }
  });
  ws.addEventListener("error", (e) => {
    log.warn({ event: "ws_error", url });
    if (cb.onError) cb.onError(e as Event);
  });
  ws.addEventListener("close", (e) => {
    const ce = e as CloseEvent;
    const wasAuth = handleDashboardStreamAuthClose(ce);
    log.info({ event: "ws_close", url, code: ce.code, reason: ce.reason, was_clean: ce.wasClean });
    cb.onClose(ce, wasAuth);
  });
  return ws;
}
