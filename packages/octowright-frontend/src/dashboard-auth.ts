/** Origin-scoped dashboard bearer lifecycle. No cookies or localStorage. */

export const DASHBOARD_AUTH_STORAGE_KEY = "octowright.dashboard.auth.v1";
export const DASHBOARD_AUTH_REQUIRED_EVENT = "octowright:dashboard-auth-required";
export const DASHBOARD_WS_PROTOCOL = "octowright.dashboard";
export const DASHBOARD_WS_BEARER_PREFIX = `${DASHBOARD_WS_PROTOCOL}.bearer.`;

export interface DashboardBearerGrant {
  bearer: string;
  expires_at: number;
}

interface StoredDashboardBearer {
  bearer: string;
  expiresAt: number;
}

interface DashboardAuthBootstrapOptions {
  fetchFn?: typeof fetch;
  history?: History;
  location?: Location;
  storage?: Storage;
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
  target.setItem(
    DASHBOARD_AUTH_STORAGE_KEY,
    JSON.stringify({ bearer: grant.bearer, expiresAt: grant.expires_at } satisfies StoredDashboardBearer),
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
  if (hadBearer && typeof window !== "undefined") {
    window.dispatchEvent(new Event(DASHBOARD_AUTH_REQUIRED_EVENT));
  }
  return hadBearer;
}

/** Fetch protected dashboard media without putting its bearer in the URL. */
export async function fetchDashboardMediaObjectUrl(
  path: string,
  options: DashboardMediaFetchOptions = {},
): Promise<string> {
  const response = await (options.fetchFn ?? fetch)(path, {
    method: "GET",
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
