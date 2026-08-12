// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0

const MEDIA_AUTH_SET = "octowright.dashboard.media-auth.set";
const MEDIA_AUTH_CLEAR = "octowright.dashboard.media-auth.clear";
const MEDIA_AUTH_READY = "octowright.dashboard.media-auth.ready";
const MEDIA_AUTH_MISSING = "octowright.dashboard.media-auth.missing";
const MEDIA_AUTH_REQUIRED = "octowright.dashboard.media-auth.required";
const MAX_MEDIA_CLIENT_CREDENTIALS = 64;
const VIDEO_PATH = /^\/api\/sessions\/[^/]+\/video$/;
const bearersByClientId = new Map();

function trimClientCredentials() {
  while (bearersByClientId.size > MAX_MEDIA_CLIENT_CREDENTIALS) {
    const oldest = bearersByClientId.keys().next().value;
    if (oldest === undefined) return;
    bearersByClientId.delete(oldest);
  }
}

async function notifyClient(clientId, message) {
  if (typeof clientId !== "string" || !clientId) return;
  const client = await globalThis.clients?.get(clientId);
  client?.postMessage(message);
}

export function handleDashboardMediaAuthMessage(event) {
  const sourceId = event?.source?.id;
  const data = event?.data;
  if (typeof sourceId !== "string" || !sourceId || !data || typeof data !== "object") return;
  if (data.type === MEDIA_AUTH_CLEAR) {
    bearersByClientId.delete(sourceId);
    return;
  }
  if (data.type === MEDIA_AUTH_SET && typeof data.bearer === "string" && data.bearer) {
    bearersByClientId.delete(sourceId);
    bearersByClientId.set(sourceId, data.bearer);
    trimClientCredentials();
    const readyPort = event.ports?.[0];
    readyPort?.postMessage({ type: MEDIA_AUTH_READY });
    readyPort?.close?.();
  }
}

export function handleDashboardMediaFetch(event) {
  const request = event?.request;
  if (!(request instanceof Request) || request.method !== "GET") return false;
  const url = new URL(request.url);
  if (url.origin !== globalThis.location.origin || !VIDEO_PATH.test(url.pathname)) return false;
  const bearer = bearersByClientId.get(event.clientId);
  if (!bearer) {
    event.respondWith(
      (async () => {
        await notifyClient(event.clientId, { type: MEDIA_AUTH_MISSING });
        return fetch(new Request(request, { cache: "no-store" }));
      })(),
    );
    return true;
  }

  const headers = new Headers(request.headers);
  headers.set("Authorization", `Bearer ${bearer}`);
  // Native media-element requests reach the worker as `no-cors`. Keeping that
  // mode would apply the no-CORS request-header guard and strip Authorization.
  // This route is already constrained to our own origin above, so forwarding
  // as `same-origin` preserves Range while allowing the bearer header.
  event.respondWith(
    (async () => {
      const response = await fetch(
        new Request(request, { cache: "no-store", headers, mode: "same-origin" }),
      );
      if (response.status === 401 || response.status === 403) {
        await notifyClient(event.clientId, { type: MEDIA_AUTH_REQUIRED, status: response.status });
      }
      return response;
    })(),
  );
  return true;
}

globalThis.addEventListener("install", (event) => {
  event.waitUntil(globalThis.skipWaiting());
});
globalThis.addEventListener("activate", (event) => {
  event.waitUntil(globalThis.clients.claim());
});
globalThis.addEventListener("message", handleDashboardMediaAuthMessage);
globalThis.addEventListener("fetch", handleDashboardMediaFetch);
