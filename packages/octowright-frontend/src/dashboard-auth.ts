/** Origin-scoped dashboard bearer lifecycle. No cookies or localStorage. */

import { clearDashboardMediaAuth } from "./dashboard-media-auth.js";

export const DASHBOARD_AUTH_STORAGE_KEY = "octowright.dashboard.auth.v1";
export const DASHBOARD_AUTH_REQUIRED_EVENT = "octowright:dashboard-auth-required";
export const DASHBOARD_WS_PROTOCOL = "octowright.dashboard";
export const DASHBOARD_WS_BEARER_PREFIX = `${DASHBOARD_WS_PROTOCOL}.bearer.`;
export const DASHBOARD_AUTH_EXPIRED_REASON = "dashboard pairing expired";
const DASHBOARD_AUTH_REQUIRED_REASON = "dashboard pairing required";
const DASHBOARD_TAB_LOCK_PREFIX = "octowright.dashboard.tab-auth.v1.";

export interface DashboardBearerGrant {
  bearer: string;
  expires_at: number;
}

interface StoredDashboardBearer {
  bearer: string;
  expiresAt: number;
  tabId: string;
}

interface DashboardAuthBootstrapOptions {
  fetchFn?: typeof fetch;
  history?: History;
  location?: Location;
  storage?: Storage;
}

interface DashboardTabIsolationOptions {
  lockManager?: DashboardTabLockManager | null;
}

interface DashboardTabLockManager {
  request(
    name: string,
    options: { ifAvailable: true; mode: "exclusive" },
    callback: (lock: object | null) => Promise<void>,
  ): Promise<void>;
}

let activeTabId: string | null = null;
let tabOwnsBearer = false;
let heldLockTabId: string | null = null;
let releaseTabLock: (() => void) | null = null;

function newTabId(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function currentLockManager(provided?: DashboardTabLockManager | null): DashboardTabLockManager | null {
  if (provided !== undefined) return provided;
  if (typeof navigator === "undefined" || !navigator.locks) return null;
  return navigator.locks as unknown as DashboardTabLockManager;
}

function releaseHeldTabLock(): void {
  releaseTabLock?.();
  releaseTabLock = null;
  heldLockTabId = null;
}

async function acquireTabLock(manager: DashboardTabLockManager, tabId: string): Promise<boolean> {
  if (heldLockTabId === tabId && releaseTabLock !== null) return true;
  releaseHeldTabLock();

  return new Promise<boolean>((resolve) => {
    let settled = false;
    const settle = (acquired: boolean): void => {
      if (settled) return;
      settled = true;
      resolve(acquired);
    };
    try {
      void manager
        .request(`${DASHBOARD_TAB_LOCK_PREFIX}${tabId}`, { ifAvailable: true, mode: "exclusive" }, async (lock) => {
          if (!lock) {
            settle(false);
            return;
          }
          heldLockTabId = tabId;
          const released = new Promise<void>((release) => {
            releaseTabLock = release;
          });
          settle(true);
          await released;
        })
        .catch(() => settle(false));
    } catch {
      settle(false);
    }
  });
}

interface DashboardMediaFetchOptions {
  fetchFn?: typeof fetch;
  signal?: AbortSignal;
}

function currentSessionStorage(storage?: Storage): Storage | null {
  if (storage) return storage;
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null;
  }
}

export function clearDashboardBearer(storage?: Storage): void {
  tabOwnsBearer = false;
  releaseHeldTabLock();
  clearDashboardMediaAuth();
  try {
    currentSessionStorage(storage)?.removeItem(DASHBOARD_AUTH_STORAGE_KEY);
  } catch {
    // A privacy-restricted browser may deny storage access. Treat it as empty.
  }
}

export function setDashboardBearer(grant: DashboardBearerGrant, storage?: Storage): void {
  if (!grant.bearer || !Number.isFinite(grant.expires_at)) {
    throw new Error("Pairing returned an invalid dashboard bearer");
  }
  const target = currentSessionStorage(storage);
  if (!target) throw new Error("Dashboard pairing requires sessionStorage");
  activeTabId ??= newTabId();
  tabOwnsBearer = true;
  target.setItem(
    DASHBOARD_AUTH_STORAGE_KEY,
    JSON.stringify({
      bearer: grant.bearer,
      expiresAt: grant.expires_at,
      tabId: activeTabId,
    } satisfies StoredDashboardBearer),
  );
}

export function getDashboardBearer(storage?: Storage): string | null {
  const target = currentSessionStorage(storage);
  if (!target) return null;
  try {
    const raw = target.getItem(DASHBOARD_AUTH_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredDashboardBearer>;
    if (
      typeof parsed.bearer !== "string" ||
      !parsed.bearer ||
      typeof parsed.expiresAt !== "number" ||
      !Number.isFinite(parsed.expiresAt) ||
      typeof parsed.tabId !== "string" ||
      !parsed.tabId ||
      parsed.expiresAt <= Date.now() / 1000
    ) {
      clearDashboardBearer(target);
      return null;
    }
    return parsed.bearer;
  } catch {
    clearDashboardBearer(target);
    return null;
  }
}

/** Claim this bearer for one browsing context and reject cloned sessionStorage. */
export async function isolateDashboardTabAuth(options: DashboardTabIsolationOptions = {}): Promise<boolean> {
  const target = currentSessionStorage();
  const raw = target?.getItem(DASHBOARD_AUTH_STORAGE_KEY);
  if (!target || !raw) return false;
  let stored: StoredDashboardBearer;
  try {
    stored = JSON.parse(raw) as StoredDashboardBearer;
  } catch {
    clearDashboardBearer(target);
    return false;
  }
  if (!stored.tabId) {
    clearDashboardBearer(target);
    return false;
  }
  const lockManager = currentLockManager(options.lockManager);
  const createdInThisDocument = activeTabId === stored.tabId && tabOwnsBearer;
  if (!lockManager) {
    if (createdInThisDocument) return false;
    clearDashboardBearer(target);
    handleDashboardUnauthorized(target);
    return true;
  }
  const acquired = await acquireTabLock(lockManager, stored.tabId);
  if (!acquired) {
    clearDashboardBearer(target);
    handleDashboardUnauthorized(target);
    return true;
  }
  activeTabId = stored.tabId;
  tabOwnsBearer = true;
  return false;
}

export function disposeDashboardTabIsolation(): void {
  releaseHeldTabLock();
  activeTabId = null;
  tabOwnsBearer = false;
  clearDashboardMediaAuth();
}

export function dashboardAuthHeaders(initial?: HeadersInit, storage?: Storage): Headers {
  const headers = new Headers(initial);
  const bearer = getDashboardBearer(storage);
  if (bearer) headers.set("Authorization", `Bearer ${bearer}`);
  return headers;
}

export function dashboardWebSocketProtocols(storage?: Storage): string[] {
  const bearer = getDashboardBearer(storage);
  return bearer ? [DASHBOARD_WS_PROTOCOL, `${DASHBOARD_WS_BEARER_PREFIX}${bearer}`] : [];
}

export function handleDashboardUnauthorized(storage?: Storage): boolean {
  const hadBearer = getDashboardBearer(storage) !== null;
  clearDashboardBearer(storage);
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(DASHBOARD_AUTH_REQUIRED_EVENT));
  }
  return hadBearer;
}

export function isDashboardAuthClose(event: Pick<CloseEvent, "code" | "reason">): boolean {
  return (
    event.code === 1008 &&
    (event.reason === DASHBOARD_AUTH_EXPIRED_REASON || event.reason === DASHBOARD_AUTH_REQUIRED_REASON)
  );
}

export function handleDashboardStreamAuthClose(event: Pick<CloseEvent, "code" | "reason">): boolean {
  if (!isDashboardAuthClose(event)) return false;
  handleDashboardUnauthorized();
  return true;
}

/** Fetch protected dashboard media without putting its bearer in the URL. */
export async function fetchDashboardMediaObjectUrl(
  path: string,
  options: DashboardMediaFetchOptions = {},
): Promise<string> {
  const response = await (options.fetchFn ?? fetch)(path, {
    method: "GET",
    cache: "no-store",
    headers: dashboardAuthHeaders(),
    ...(options.signal ? { signal: options.signal } : {}),
  });
  if (response.status === 401) handleDashboardUnauthorized();
  if (!response.ok) throw new Error(`media request failed (${response.status})`);
  return URL.createObjectURL(await response.blob());
}

/** Download a protected response via a short-lived local blob URL. */
export async function downloadDashboardMedia(
  path: string,
  filename: string,
  options: DashboardMediaFetchOptions = {},
): Promise<void> {
  const objectUrl = await fetchDashboardMediaObjectUrl(path, options);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  link.hidden = true;
  (document.body ?? document.documentElement).append(link);
  try {
    link.click();
  } finally {
    link.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
  }
}

export async function bootstrapDashboardAuth(options: DashboardAuthBootstrapOptions = {}): Promise<boolean> {
  const currentLocation = options.location ?? window.location;
  if (currentLocation.pathname !== "/pair" || currentLocation.hash.length <= 1) return false;

  const currentHistory = options.history ?? window.history;
  const code = currentLocation.hash.slice(1);
  // Scrub before the first await so neither success nor failure leaves the
  // one-time credential in history or the address bar.
  currentHistory.replaceState(null, "", "/pair");

  const fetchFn = options.fetchFn ?? fetch;
  const response = await fetchFn("/api/pair/redeem", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (!response.ok) {
    throw new Error(`Pairing failed (${response.status}). Run \`octowright dashboard\` for a fresh URL.`);
  }
  const grant = (await response.json()) as Partial<DashboardBearerGrant>;
  if (typeof grant.bearer !== "string" || typeof grant.expires_at !== "number") {
    throw new Error("Pairing failed: the leader returned an invalid response");
  }
  setDashboardBearer({ bearer: grant.bearer, expires_at: grant.expires_at }, options.storage);
  currentHistory.replaceState(null, "", "/");
  return true;
}
