import { afterEach, describe, expect, it, vi } from "vitest";
import {
  clearDashboardMediaAuth,
  configureDashboardMediaAuth,
} from "./dashboard-media-auth.js";

interface FakeController {
  postMessage: ReturnType<typeof vi.fn>;
}

function controlledServiceWorker() {
  const controller: FakeController = { postMessage: vi.fn() };
  const serviceWorker = new EventTarget() as EventTarget & {
    controller: FakeController | null;
    register: ReturnType<typeof vi.fn>;
  };
  serviceWorker.controller = controller;
  serviceWorker.register = vi.fn(async () => ({}));
  return { controller, serviceWorker };
}

class FakeMessagePort extends EventTarget {
  close = vi.fn();
  postMessage = vi.fn();
  start = vi.fn();
}

class FakeMessageChannel {
  static instances: FakeMessageChannel[] = [];
  port1 = new FakeMessagePort();
  port2 = new FakeMessagePort();

  constructor() {
    FakeMessageChannel.instances.push(this);
  }
}

afterEach(() => {
  clearDashboardMediaAuth();
  FakeMessageChannel.instances = [];
  vi.unstubAllGlobals();
});

describe("dashboard media service-worker coordinator", () => {
  it("registers a module worker and posts only this page's bearer to its controller", async () => {
    const { controller, serviceWorker } = controlledServiceWorker();
    vi.stubGlobal("MessageChannel", FakeMessageChannel);

    let settled = false;
    const configuring = configureDashboardMediaAuth("page-secret", {
      serviceWorker: serviceWorker as unknown as ServiceWorkerContainer,
    }).then(() => {
      settled = true;
    });
    for (let index = 0; index < 4; index += 1) await Promise.resolve();

    expect(serviceWorker.register).toHaveBeenCalledWith("/dashboard-media-sw.js", {
      scope: "/",
      type: "module",
    });
    expect(settled).toBe(false);
    const channel = FakeMessageChannel.instances[0];
    expect(channel).toBeDefined();
    expect(controller.postMessage).toHaveBeenCalledWith(
      {
        type: "octowright.dashboard.media-auth.set",
        bearer: "page-secret",
      },
      [channel?.port2],
    );

    channel?.port1.dispatchEvent(
      new MessageEvent("message", { data: { type: "octowright.dashboard.media-auth.ready" } }),
    );
    await configuring;
    expect(settled).toBe(true);
  });

  it("clears the current client's worker credential on teardown", async () => {
    const { controller, serviceWorker } = controlledServiceWorker();
    vi.stubGlobal("MessageChannel", FakeMessageChannel);
    const configuring = configureDashboardMediaAuth("page-secret", {
      serviceWorker: serviceWorker as unknown as ServiceWorkerContainer,
    });
    for (let index = 0; index < 4; index += 1) await Promise.resolve();
    FakeMessageChannel.instances[0]?.port1.dispatchEvent(
      new MessageEvent("message", { data: { type: "octowright.dashboard.media-auth.ready" } }),
    );
    await configuring;

    clearDashboardMediaAuth({ serviceWorker: serviceWorker as unknown as ServiceWorkerContainer });

    expect(controller.postMessage).toHaveBeenLastCalledWith({
      type: "octowright.dashboard.media-auth.clear",
    });
  });

  it("restores only this page's bearer when a restarted worker reports missing state", async () => {
    const { controller, serviceWorker } = controlledServiceWorker();
    vi.stubGlobal("MessageChannel", FakeMessageChannel);
    const onRecovered = vi.fn();
    const configuring = configureDashboardMediaAuth("restart-secret", {
      serviceWorker: serviceWorker as unknown as ServiceWorkerContainer,
      onRecovered,
    });
    for (let index = 0; index < 4; index += 1) await Promise.resolve();
    FakeMessageChannel.instances[0]?.port1.dispatchEvent(
      new MessageEvent("message", { data: { type: "octowright.dashboard.media-auth.ready" } }),
    );
    await configuring;
    controller.postMessage.mockClear();

    serviceWorker.dispatchEvent(
      new MessageEvent("message", {
        source: controller as unknown as ServiceWorker,
        data: { type: "octowright.dashboard.media-auth.missing" },
      }),
    );
    for (let index = 0; index < 4; index += 1) await Promise.resolve();

    expect(controller.postMessage).toHaveBeenCalledWith(
      { type: "octowright.dashboard.media-auth.set", bearer: "restart-secret" },
      [FakeMessageChannel.instances[1]?.port2],
    );
    expect(onRecovered).not.toHaveBeenCalled();
    FakeMessageChannel.instances[1]?.port1.dispatchEvent(
      new MessageEvent("message", { data: { type: "octowright.dashboard.media-auth.ready" } }),
    );
    await vi.waitFor(() => expect(onRecovered).toHaveBeenCalledOnce());
  });

  it("coalesces duplicate missing-state messages from one controller", async () => {
    const { controller, serviceWorker } = controlledServiceWorker();
    vi.stubGlobal("MessageChannel", FakeMessageChannel);
    const onRecovered = vi.fn();
    const configuring = configureDashboardMediaAuth("coalesce-secret", {
      serviceWorker: serviceWorker as unknown as ServiceWorkerContainer,
      onRecovered,
    });
    for (let index = 0; index < 4; index += 1) await Promise.resolve();
    FakeMessageChannel.instances[0]?.port1.dispatchEvent(
      new MessageEvent("message", { data: { type: "octowright.dashboard.media-auth.ready" } }),
    );
    await configuring;

    const missing = new MessageEvent("message", {
      source: controller as unknown as ServiceWorker,
      data: { type: "octowright.dashboard.media-auth.missing" },
    });
    serviceWorker.dispatchEvent(missing);
    serviceWorker.dispatchEvent(missing);
    for (let index = 0; index < 4; index += 1) await Promise.resolve();
    expect(FakeMessageChannel.instances).toHaveLength(2);
    FakeMessageChannel.instances[1]?.port1.dispatchEvent(
      new MessageEvent("message", { data: { type: "octowright.dashboard.media-auth.ready" } }),
    );
    await vi.waitFor(() => expect(onRecovered).toHaveBeenCalledOnce());
    expect(FakeMessageChannel.instances).toHaveLength(2);
  });

  it("surfaces a bounded recovery acknowledgement timeout", async () => {
    const { controller, serviceWorker } = controlledServiceWorker();
    vi.stubGlobal("MessageChannel", FakeMessageChannel);
    const onRecoveryFailed = vi.fn();
    const configuring = configureDashboardMediaAuth("lost-ack-secret", {
      serviceWorker: serviceWorker as unknown as ServiceWorkerContainer,
      timeoutMs: 5,
      onRecoveryFailed,
    });
    for (let index = 0; index < 4; index += 1) await Promise.resolve();
    FakeMessageChannel.instances[0]?.port1.dispatchEvent(
      new MessageEvent("message", { data: { type: "octowright.dashboard.media-auth.ready" } }),
    );
    await configuring;

    serviceWorker.dispatchEvent(
      new MessageEvent("message", {
        source: controller as unknown as ServiceWorker,
        data: { type: "octowright.dashboard.media-auth.missing" },
      }),
    );

    await vi.waitFor(() => expect(onRecoveryFailed).toHaveBeenCalledOnce());
    expect(onRecoveryFailed.mock.calls[0]?.[0]?.message).toContain("acknowledge");
  });

  it("reauthorizes the replacement controller after a worker update", async () => {
    const { serviceWorker } = controlledServiceWorker();
    const initialController = serviceWorker.controller;
    vi.stubGlobal("MessageChannel", FakeMessageChannel);
    const onRecovered = vi.fn();
    const configuring = configureDashboardMediaAuth("update-secret", {
      serviceWorker: serviceWorker as unknown as ServiceWorkerContainer,
      onRecovered,
    });
    for (let index = 0; index < 4; index += 1) await Promise.resolve();
    FakeMessageChannel.instances[0]?.port1.dispatchEvent(
      new MessageEvent("message", { data: { type: "octowright.dashboard.media-auth.ready" } }),
    );
    await configuring;

    const replacement: FakeController = { postMessage: vi.fn() };
    serviceWorker.controller = replacement;
    serviceWorker.dispatchEvent(new Event("controllerchange"));
    for (let index = 0; index < 4; index += 1) await Promise.resolve();

    expect(initialController?.postMessage).toHaveBeenCalledTimes(1);
    expect(replacement.postMessage).toHaveBeenCalledWith(
      { type: "octowright.dashboard.media-auth.set", bearer: "update-secret" },
      [FakeMessageChannel.instances[1]?.port2],
    );
    FakeMessageChannel.instances[1]?.port1.dispatchEvent(
      new MessageEvent("message", { data: { type: "octowright.dashboard.media-auth.ready" } }),
    );
    await vi.waitFor(() => expect(onRecovered).toHaveBeenCalledOnce());
  });

  it("retries recovery on a replacement controller when it changes before the first ack", async () => {
    const { controller, serviceWorker } = controlledServiceWorker();
    vi.stubGlobal("MessageChannel", FakeMessageChannel);
    const onRecovered = vi.fn();
    const configuring = configureDashboardMediaAuth("overlap-secret", {
      serviceWorker: serviceWorker as unknown as ServiceWorkerContainer,
      onRecovered,
    });
    for (let index = 0; index < 4; index += 1) await Promise.resolve();
    FakeMessageChannel.instances[0]?.port1.dispatchEvent(
      new MessageEvent("message", { data: { type: "octowright.dashboard.media-auth.ready" } }),
    );
    await configuring;

    serviceWorker.dispatchEvent(
      new MessageEvent("message", {
        source: controller as unknown as ServiceWorker,
        data: { type: "octowright.dashboard.media-auth.missing" },
      }),
    );
    for (let index = 0; index < 4; index += 1) await Promise.resolve();
    const replacement: FakeController = { postMessage: vi.fn() };
    serviceWorker.controller = replacement;
    serviceWorker.dispatchEvent(new Event("controllerchange"));

    // Resolve the stale controller's in-flight recovery; the queued controller
    // change must immediately start a fresh authorization against replacement.
    FakeMessageChannel.instances[1]?.port1.dispatchEvent(
      new MessageEvent("message", { data: { type: "octowright.dashboard.media-auth.ready" } }),
    );
    await vi.waitFor(() => expect(FakeMessageChannel.instances).toHaveLength(3));
    expect(replacement.postMessage).toHaveBeenCalledWith(
      { type: "octowright.dashboard.media-auth.set", bearer: "overlap-secret" },
      [FakeMessageChannel.instances[2]?.port2],
    );
    FakeMessageChannel.instances[2]?.port1.dispatchEvent(
      new MessageEvent("message", { data: { type: "octowright.dashboard.media-auth.ready" } }),
    );
    await vi.waitFor(() => expect(onRecovered).toHaveBeenCalledOnce());
  });

  it.each([401, 403])("reports worker video auth status %s without attempting recovery", async (status) => {
    const { controller, serviceWorker } = controlledServiceWorker();
    vi.stubGlobal("MessageChannel", FakeMessageChannel);
    const onRecovered = vi.fn();
    const onUnauthorized = vi.fn();
    const configuring = configureDashboardMediaAuth("expired-secret", {
      serviceWorker: serviceWorker as unknown as ServiceWorkerContainer,
      onRecovered,
      onUnauthorized,
    });
    for (let index = 0; index < 4; index += 1) await Promise.resolve();
    FakeMessageChannel.instances[0]?.port1.dispatchEvent(
      new MessageEvent("message", { data: { type: "octowright.dashboard.media-auth.ready" } }),
    );
    await configuring;
    controller.postMessage.mockClear();

    serviceWorker.dispatchEvent(
      new MessageEvent("message", {
        source: controller as unknown as ServiceWorker,
        data: { type: "octowright.dashboard.media-auth.required", status },
      }),
    );
    await Promise.resolve();

    expect(onUnauthorized).toHaveBeenCalledWith(status);
    expect(onRecovered).not.toHaveBeenCalled();
    expect(controller.postMessage).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: "octowright.dashboard.media-auth.set" }),
      expect.anything(),
    );
  });

  it("fails boundedly when service workers are unavailable", async () => {
    await expect(configureDashboardMediaAuth("page-secret", { serviceWorker: null })).rejects.toThrow(
      "service worker",
    );
  });

  it("times out a registration that never settles", async () => {
    const { serviceWorker } = controlledServiceWorker();
    serviceWorker.register.mockImplementation(() => new Promise(() => undefined));

    await expect(
      configureDashboardMediaAuth("page-secret", {
        serviceWorker: serviceWorker as unknown as ServiceWorkerContainer,
        timeoutMs: 1,
      }),
    ).rejects.toThrow("registration timed out");
  });
});

describe("dashboard media service worker", () => {
  it("bypasses caches and asks only the originating client to restore a lost bearer", async () => {
    const worker = await import("../static/dashboard-media-sw.js");
    const postMessage = vi.fn();
    const getClient = vi.fn(async (clientId: string) =>
      clientId === "client-after-restart" ? { postMessage } : undefined,
    );
    vi.stubGlobal("clients", { get: getClient });
    const fetchFn = vi.fn(async () => new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetchFn);
    let response: Promise<Response> | null = null;
    const request = new Request(`${window.location.origin}/api/sessions/session-restart/video`, {
      mode: "no-cors",
    });

    const handled = worker.handleDashboardMediaFetch({
      clientId: "client-after-restart",
      request,
      respondWith(value: Promise<Response>) {
        response = value;
      },
    });

    expect(handled).toBe(true);
    await response;
    const forwarded = fetchFn.mock.calls[0]?.[0] as Request;
    expect(forwarded.cache).toBe("no-store");
    expect(forwarded.headers.has("Authorization")).toBe(false);
    expect(getClient).toHaveBeenCalledWith("client-after-restart");
    expect(postMessage).toHaveBeenCalledWith({
      type: "octowright.dashboard.media-auth.missing",
    });
  });

  it("preserves Range while adding only the matching Client.id bearer", async () => {
    const worker = await import("../static/dashboard-media-sw.js");
    worker.handleDashboardMediaAuthMessage({
      source: { id: "client-a" },
      data: { type: "octowright.dashboard.media-auth.set", bearer: "client-a-secret" },
    });
    const fetchFn = vi.fn(async () => new Response(null, { status: 206 }));
    vi.stubGlobal("fetch", fetchFn);
    let response: Promise<Response> | null = null;
    const request = new Request(`${window.location.origin}/api/sessions/session-1/video`, {
      mode: "no-cors",
      headers: { Range: "bytes=4096-8191" },
    });

    const handled = worker.handleDashboardMediaFetch({
      clientId: "client-a",
      request,
      respondWith(value: Promise<Response>) {
        response = value;
      },
    });

    expect(handled).toBe(true);
    await response;
    const forwarded = fetchFn.mock.calls[0]?.[0] as Request;
    expect(request.mode).toBe("no-cors");
    expect(forwarded.mode).toBe("same-origin");
    expect(forwarded.headers.get("Range")).toBe("bytes=4096-8191");
    expect(forwarded.headers.get("Authorization")).toBe("Bearer client-a-secret");
  });

  it("does not let another or cleared Client.id inherit a bearer", async () => {
    const worker = await import("../static/dashboard-media-sw.js");
    const request = new Request(`${window.location.origin}/api/sessions/session-2/video`);
    const fetchFn = vi.fn(async () => new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetchFn);
    vi.stubGlobal("clients", { get: vi.fn(async () => undefined) });
    worker.handleDashboardMediaAuthMessage({
      source: { id: "client-owner" },
      data: { type: "octowright.dashboard.media-auth.set", bearer: "owner-secret" },
    });
    const responses: Promise<Response>[] = [];
    const respondWith = (response: Promise<Response>): void => {
      responses.push(response);
    };

    expect(worker.handleDashboardMediaFetch({ clientId: "client-duplicate", request, respondWith })).toBe(true);
    await responses.shift();
    expect((fetchFn.mock.calls[0]?.[0] as Request).headers.has("Authorization")).toBe(false);

    worker.handleDashboardMediaAuthMessage({
      source: { id: "client-owner" },
      data: { type: "octowright.dashboard.media-auth.clear" },
    });
    expect(worker.handleDashboardMediaFetch({ clientId: "client-owner", request, respondWith })).toBe(true);
    await responses.shift();
    expect((fetchFn.mock.calls[1]?.[0] as Request).headers.has("Authorization")).toBe(false);
  });

  it.each([401, 403])("notifies only the originating client when authenticated video returns %s", async (status) => {
    const worker = await import("../static/dashboard-media-sw.js");
    const postMessage = vi.fn();
    const getClient = vi.fn(async (clientId: string) =>
      clientId === "client-denied" ? { postMessage } : undefined,
    );
    vi.stubGlobal("clients", { get: getClient });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status })));
    worker.handleDashboardMediaAuthMessage({
      source: { id: "client-denied" },
      data: { type: "octowright.dashboard.media-auth.set", bearer: "denied-secret" },
    });
    let response: Promise<Response> | null = null;

    worker.handleDashboardMediaFetch({
      clientId: "client-denied",
      request: new Request(`${window.location.origin}/api/sessions/session-denied/video`),
      respondWith(value: Promise<Response>) {
        response = value;
      },
    });

    expect((await response)?.status).toBe(status);
    expect(getClient).toHaveBeenCalledWith("client-denied");
    expect(postMessage).toHaveBeenCalledWith({
      type: "octowright.dashboard.media-auth.required",
      status,
    });
  });

  it("acknowledges only after storing the source Client.id bearer", async () => {
    const worker = await import("../static/dashboard-media-sw.js");
    const ackPort = { postMessage: vi.fn(), close: vi.fn() };

    worker.handleDashboardMediaAuthMessage({
      source: { id: "client-ack" },
      data: { type: "octowright.dashboard.media-auth.set", bearer: "ack-secret" },
      ports: [ackPort],
    });

    expect(ackPort.postMessage).toHaveBeenCalledWith({ type: "octowright.dashboard.media-auth.ready" });
    const fetchFn = vi.fn(async () => new Response(null, { status: 206 }));
    vi.stubGlobal("fetch", fetchFn);
    let response: Promise<Response> | null = null;
    worker.handleDashboardMediaFetch({
      clientId: "client-ack",
      request: new Request(`${window.location.origin}/api/sessions/session-ack/video`),
      respondWith(value: Promise<Response>) {
        response = value;
      },
    });
    await response;
    expect((fetchFn.mock.calls[0]?.[0] as Request).headers.get("Authorization")).toBe("Bearer ack-secret");
  });

  it("bounds stale Client.id credentials so old tabs cannot grow worker memory forever", async () => {
    const worker = await import("../static/dashboard-media-sw.js");
    const fetchFn = vi.fn(async () => new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetchFn);
    vi.stubGlobal("clients", { get: vi.fn(async () => undefined) });
    for (let index = 0; index < 65; index += 1) {
      worker.handleDashboardMediaAuthMessage({
        source: { id: `bounded-client-${index}` },
        data: { type: "octowright.dashboard.media-auth.set", bearer: `secret-${index}` },
      });
    }
    let response: Promise<Response> | null = null;

    expect(
      worker.handleDashboardMediaFetch({
        clientId: "bounded-client-0",
        request: new Request(`${window.location.origin}/api/sessions/session-old/video`),
        respondWith(value: Promise<Response>) {
          response = value;
        },
      }),
    ).toBe(true);
    await response;
    expect((fetchFn.mock.calls[0]?.[0] as Request).headers.has("Authorization")).toBe(false);
  });
});
