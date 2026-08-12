import { dashboardWebSocketProtocols, handleDashboardStreamAuthClose } from "./dashboard-auth.js";
import { getLogger, wsConnectsCounter, wsMessagesCounter } from "./telemetry.js";

const log = getLogger("octowright.frontend.screencast");

export interface ScreencastHandle {
  close(): void;
}

export interface ScreencastOptions {
  onFrame: (blob: Blob) => void;
  onError?: (event: Event) => void;
  onClose?: (event: CloseEvent) => void;
  /** Inject a WebSocket constructor — useful in tests. */
  webSocketCtor?: typeof WebSocket;
}

export function openScreencast(url: string, opts: ScreencastOptions): ScreencastHandle {
  const Ctor = opts.webSocketCtor ?? WebSocket;
  const protocols = dashboardWebSocketProtocols();
  const ws = protocols.length > 0 ? new Ctor(url, protocols) : new Ctor(url);
  ws.binaryType = "blob";

  log.info({ event: "ws_connecting", kind: "screencast", url });
  ws.addEventListener("open", () => {
    wsConnectsCounter.add(1, { kind: "screencast" });
    log.info({ event: "ws_connect", kind: "screencast", url });
  });
  ws.addEventListener("message", (raw) => {
    const data = (raw as MessageEvent).data;
    if (!(data instanceof Blob)) return;
    wsMessagesCounter.add(1, { kind: "screencast" });
    log.debug({ event: "ws_message", kind: "screencast", bytes: data.size });
    opts.onFrame(data);
  });
  ws.addEventListener("error", (e) => {
    log.warn({ event: "ws_error", kind: "screencast", url });
    if (opts.onError) opts.onError(e as Event);
  });
  ws.addEventListener("close", (e) => {
    const ce = e as CloseEvent;
    handleDashboardStreamAuthClose(ce);
    log.info({
      event: "ws_close",
      kind: "screencast",
      url,
      code: ce.code,
      reason: ce.reason,
      was_clean: ce.wasClean,
    });
    if (opts.onClose) opts.onClose(ce);
  });

  return {
    close() {
      try {
        ws.close();
        log.debug({ event: "ws_close_requested", kind: "screencast", url });
      } catch (err) {
        log.debug({ event: "ws_close_failed", kind: "screencast", url, error: String(err) });
      }
    },
  };
}
