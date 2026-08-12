/** Client-scoped service-worker authorization for native Range video playback. */

const DASHBOARD_MEDIA_WORKER_URL = "/dashboard-media-sw.js";
const DASHBOARD_MEDIA_WORKER_SCOPE = "/";
const DASHBOARD_MEDIA_CONTROL_TIMEOUT_MS = 5000;
const MEDIA_AUTH_SET = "octowright.dashboard.media-auth.set";
const MEDIA_AUTH_CLEAR = "octowright.dashboard.media-auth.clear";
const MEDIA_AUTH_READY = "octowright.dashboard.media-auth.ready";
const MEDIA_AUTH_MISSING = "octowright.dashboard.media-auth.missing";
const MEDIA_AUTH_REQUIRED = "octowright.dashboard.media-auth.required";

export interface DashboardMediaAuthOptions {
  serviceWorker?: ServiceWorkerContainer | null;
  signal?: AbortSignal;
  timeoutMs?: number;
  setTimeoutFn?: (callback: () => void, delay: number) => ReturnType<typeof setTimeout>;
  clearTimeoutFn?: (timer: ReturnType<typeof setTimeout>) => void;
  onRecovered?: () => void;
  onRecoveryFailed?: (error: Error) => void;
  onUnauthorized?: (status: 401 | 403) => void;
}

interface ActiveMediaAuthorization {
  bearer: string;
  container: ServiceWorkerContainer;
  generation: number;
  onControllerChange: () => void;
  onMessage: (event: MessageEvent) => void;
  options: DashboardMediaAuthOptions;
  recovery: Promise<void> | null;
  recoveryController: ServiceWorker | null;
  recoveryQueued: boolean;
}

let generation = 0;
let activeContainer: ServiceWorkerContainer | null = null;
let cancelPendingAuthorization: (() => void) | null = null;
let activeAuthorization: ActiveMediaAuthorization | null = null;

function currentServiceWorker(options: DashboardMediaAuthOptions): ServiceWorkerContainer | null {
  if (options.serviceWorker !== undefined) return options.serviceWorker;
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return null;
  return navigator.serviceWorker;
}

function abortedError(): DOMException {
  return new DOMException("dashboard media authorization was cleared", "AbortError");
}

function waitForRegistration(
  registration: Promise<ServiceWorkerRegistration>,
  options: DashboardMediaAuthOptions,
): Promise<ServiceWorkerRegistration> {
  if (options.signal?.aborted) return Promise.reject(abortedError());
  const setTimer = options.setTimeoutFn ?? ((callback, delay) => setTimeout(callback, delay));
  const clearTimer = options.clearTimeoutFn ?? ((timer) => clearTimeout(timer));
  const timeoutMs = Math.max(1, options.timeoutMs ?? DASHBOARD_MEDIA_CONTROL_TIMEOUT_MS);
  return new Promise<ServiceWorkerRegistration>((resolve, reject) => {
    let settled = false;
    const finish = (value?: ServiceWorkerRegistration, error?: Error): void => {
      if (settled) return;
      settled = true;
      clearTimer(timer);
      options.signal?.removeEventListener("abort", onAbort);
      if (value) resolve(value);
      else reject(error ?? new Error("Paired video service worker registration failed."));
    };
    const onAbort = (): void => finish(undefined, abortedError());
    const timer = setTimer(
      () => finish(undefined, new Error("Paired video service worker registration timed out.")),
      timeoutMs,
    );
    options.signal?.addEventListener("abort", onAbort, { once: true });
    registration.then(
      (value) => finish(value),
      (error: unknown) => finish(undefined, error instanceof Error ? error : new Error(String(error))),
    );
  });
}

function waitForController(
  container: ServiceWorkerContainer,
  options: DashboardMediaAuthOptions,
): Promise<ServiceWorker> {
  if (container.controller) return Promise.resolve(container.controller);
  if (options.signal?.aborted) return Promise.reject(abortedError());
  const setTimer = options.setTimeoutFn ?? ((callback, delay) => setTimeout(callback, delay));
  const clearTimer = options.clearTimeoutFn ?? ((timer) => clearTimeout(timer));
  const timeoutMs = Math.max(1, options.timeoutMs ?? DASHBOARD_MEDIA_CONTROL_TIMEOUT_MS);

  return new Promise<ServiceWorker>((resolve, reject) => {
    let settled = false;
    const finish = (controller: ServiceWorker | null, error?: Error): void => {
      if (settled) return;
      settled = true;
      clearTimer(timer);
      container.removeEventListener("controllerchange", onControllerChange);
      options.signal?.removeEventListener("abort", onAbort);
      if (controller) resolve(controller);
      else reject(error ?? new Error("Paired video service worker could not take control."));
    };
    const onControllerChange = (): void => {
      if (container.controller) finish(container.controller);
    };
    const onAbort = (): void => finish(null, abortedError());
    const timer = setTimer(
      () => finish(null, new Error("Paired video service worker could not take control.")),
      timeoutMs,
    );
    container.addEventListener("controllerchange", onControllerChange);
    options.signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function waitForAuthorizationReady(
  controller: ServiceWorker,
  bearer: string,
  options: DashboardMediaAuthOptions,
): Promise<void> {
  if (options.signal?.aborted) return Promise.reject(abortedError());
  if (typeof MessageChannel === "undefined") {
    return Promise.reject(new Error("Paired video streaming requires MessageChannel support."));
  }
  const setTimer = options.setTimeoutFn ?? ((callback, delay) => setTimeout(callback, delay));
  const clearTimer = options.clearTimeoutFn ?? ((timer) => clearTimeout(timer));
  const timeoutMs = Math.max(1, options.timeoutMs ?? DASHBOARD_MEDIA_CONTROL_TIMEOUT_MS);
  const channel = new MessageChannel();

  return new Promise<void>((resolve, reject) => {
    let settled = false;
    const finish = (error?: Error): void => {
      if (settled) return;
      settled = true;
      clearTimer(timer);
      channel.port1.removeEventListener("message", onMessage);
      options.signal?.removeEventListener("abort", onAbort);
      channel.port1.close();
      if (cancelPendingAuthorization === cancel) cancelPendingAuthorization = null;
      if (error) reject(error);
      else resolve();
    };
    const onMessage = (event: MessageEvent): void => {
      const data = event.data as { type?: unknown } | null;
      if (data?.type === MEDIA_AUTH_READY) finish();
    };
    const onAbort = (): void => finish(abortedError());
    const cancel = (): void => finish(abortedError());
    const timer = setTimer(
      () => finish(new Error("Paired video service worker did not acknowledge authorization.")),
      timeoutMs,
    );
    cancelPendingAuthorization?.();
    cancelPendingAuthorization = cancel;
    channel.port1.addEventListener("message", onMessage);
    channel.port1.start();
    options.signal?.addEventListener("abort", onAbort, { once: true });
    controller.postMessage({ type: MEDIA_AUTH_SET, bearer }, [channel.port2]);
  });
}

function detachActiveAuthorization(): ActiveMediaAuthorization | null {
  const active = activeAuthorization;
  if (!active) return null;
  active.container.removeEventListener("message", active.onMessage);
  active.container.removeEventListener("controllerchange", active.onControllerChange);
  activeAuthorization = null;
  return active;
}

function recoverActiveAuthorization(active: ActiveMediaAuthorization): void {
  if (activeAuthorization !== active || active.generation !== generation) return;
  const controller = active.container.controller;
  if (!controller) return;
  if (active.recovery) {
    if (active.recoveryController !== controller) active.recoveryQueued = true;
    return;
  }
  active.recoveryController = controller;
  active.recovery = waitForAuthorizationReady(controller, active.bearer, active.options)
    .then(() => {
      if (
        activeAuthorization === active &&
        active.generation === generation &&
        active.container.controller === controller
      ) {
        active.options.onRecovered?.();
      }
    })
    .catch((error: unknown) => {
      if (activeAuthorization === active && !active.recoveryQueued) {
        active.options.onRecoveryFailed?.(
          error instanceof Error ? error : new Error(String(error)),
        );
      }
    })
    .finally(() => {
      if (activeAuthorization !== active) return;
      active.recovery = null;
      active.recoveryController = null;
      if (active.recoveryQueued) {
        active.recoveryQueued = false;
        recoverActiveAuthorization(active);
      }
    });
}

function attachActiveAuthorization(
  container: ServiceWorkerContainer,
  bearer: string,
  options: DashboardMediaAuthOptions,
  requestGeneration: number,
): void {
  detachActiveAuthorization();
  const active = {
    bearer,
    container,
    generation: requestGeneration,
    options,
    recovery: null,
    recoveryController: null,
    recoveryQueued: false,
    onControllerChange: () => recoverActiveAuthorization(active),
    onMessage: (event: MessageEvent) => {
      if (event.source !== container.controller) return;
      const data = event.data as { status?: unknown; type?: unknown } | null;
      if (data?.type === MEDIA_AUTH_MISSING) {
        recoverActiveAuthorization(active);
        return;
      }
      if (
        data?.type === MEDIA_AUTH_REQUIRED &&
        (data.status === 401 || data.status === 403)
      ) {
        options.onUnauthorized?.(data.status);
      }
    },
  } satisfies ActiveMediaAuthorization;
  activeAuthorization = active;
  container.addEventListener("message", active.onMessage);
  container.addEventListener("controllerchange", active.onControllerChange);
}

export async function configureDashboardMediaAuth(
  bearer: string,
  options: DashboardMediaAuthOptions = {},
): Promise<void> {
  if (!bearer) throw new Error("Paired video requires a dashboard bearer.");
  const container = currentServiceWorker(options);
  if (!container) {
    throw new Error("Paired video streaming requires service worker support.");
  }
  const requestGeneration = ++generation;
  await waitForRegistration(
    container.register(DASHBOARD_MEDIA_WORKER_URL, {
      scope: DASHBOARD_MEDIA_WORKER_SCOPE,
      type: "module",
    }),
    options,
  );
  if (requestGeneration !== generation || options.signal?.aborted) throw abortedError();
  const controller = await waitForController(container, options);
  if (requestGeneration !== generation || options.signal?.aborted) throw abortedError();
  activeContainer = container;
  await waitForAuthorizationReady(controller, bearer, options);
  if (requestGeneration !== generation || options.signal?.aborted) throw abortedError();
  attachActiveAuthorization(container, bearer, options, requestGeneration);
}

export function clearDashboardMediaAuth(options: DashboardMediaAuthOptions = {}): void {
  generation += 1;
  cancelPendingAuthorization?.();
  cancelPendingAuthorization = null;
  const active = detachActiveAuthorization();
  const container = active?.container ?? currentServiceWorker(options) ?? activeContainer;
  container?.controller?.postMessage({ type: MEDIA_AUTH_CLEAR });
  activeContainer = null;
}
