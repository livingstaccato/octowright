// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

(() => {
    if (window.__octowrightInteractions) return;
    const configBinding = window.__octowrightInteractionConfig;
    const eventBinding = window.__octowrightInteractionEvent;
    const ROOT = "__octowright_interactions__";
    const MAP = "octowright:interaction:map";
    const MAPPED = "octowright:interaction:mapped";
    const now = () => performance.now();
    const epoch = () => performance.timeOrigin + now();
    let show = false;
    let record = false;
    let destroyed = false;
    let revision = 0;
    let configured = false;
    /** @type {HTMLElement | null} */
    let root = null;
    /** @type {ShadowRoot | null} */
    let shadow = null;
    /** @typedef {{payload: OctowrightInteractionPayload, startX:number, startY:number, started:number, moved:boolean, multi:boolean, cancelled:boolean, lastMove:number, marker:HTMLElement|null, hold:number}} Contact */
    /** @type {Map<number, Contact>} */
    const contacts = new Map();
    /** @type {Set<number>} */
    const timers = new Set();
    /** @type {Map<number, Geometry>} */
    const mappedContacts = new Map();
    /** @type {Map<HTMLElement, number>} */
    const fading = new Map();
    /** @type {{at:number,x:number,y:number,type:string,button:number,marker:HTMLElement|null}|null} */
    let lastTap = null;
    /** @type {{payload:OctowrightInteractionPayload,marker:HTMLElement|null,previous:HTMLElement|null,doubled:boolean}|null} */
    let mouseRelease = null;
    /** @type {Map<string, {finish:(geometry:Geometry)=>void,timer:number}>} */
    const pending = new Map();
    /** @typedef {{x:number,y:number,width:number,height:number}} Geometry */
    /** @type {Array<[string, EventListener]>} */
    const listeners = [];

    const later = (callback, delay) => {
        const timer = window.setTimeout(() => {
            timers.delete(timer);
            callback();
        }, delay);
        timers.add(timer);
        return timer;
    };
    const stop = (timer) => {
        window.clearTimeout(timer);
        timers.delete(timer);
    };
    const finite = (value) => typeof value === "number" && Number.isFinite(value);
    const geometry = (value) =>
        value &&
        [value.x, value.y, value.width, value.height].every(finite) &&
        value.width > 0 &&
        value.height > 0 &&
        value.width <= 100000 &&
        value.height <= 100000 &&
        Math.abs(value.x) <= 1000000 &&
        Math.abs(value.y) <= 1000000;
    const viewport = () => ({ width: window.innerWidth, height: window.innerHeight });

    // Each hop handles geometry only. A reply can release only an observation
    // already captured locally, never a page-supplied interaction payload.
    const mapUp = (point, finish, depth = 0) => {
        if (destroyed || !geometry(point)) return;
        if (window.parent === window) {
            finish({ ...point, ...viewport() });
            return;
        }
        if (pending.size >= 32 || depth >= 16) return;
        // getRandomValues also works on ordinary HTTP pages, where randomUUID is absent.
        const id = Array.from(window.crypto.getRandomValues(new Uint32Array(4)), (part) =>
            part.toString(16),
        ).join("-");
        const timer = later(() => pending.delete(id), 1000);
        pending.set(id, { finish, timer });
        window.parent.postMessage({ type: MAP, id, ...point, depth }, "*");
    };
    let relayWindow = 0;
    let relayCount = 0;
    /** @param {MessageEvent} event */
    const relay = (event) => {
        const data = event.data;
        if (
            !data ||
            typeof data !== "object" ||
            !geometry(data) ||
            typeof data.id !== "string" ||
            data.id.length > 80
        )
            return;
        if (data.type === MAPPED) {
            if (event.source !== window.parent) return;
            const item = pending.get(data.id);
            if (!item) return;
            pending.delete(data.id);
            stop(item.timer);
            item.finish({ x: data.x, y: data.y, width: data.width, height: data.height });
            return;
        }
        if (data.type !== MAP || !Number.isInteger(data.depth) || data.depth < 0 || data.depth >= 16) return;
        if (now() - relayWindow >= 1000) {
            relayWindow = now();
            relayCount = 0;
        }
        if (++relayCount > 240) return;
        const frame = [...document.querySelectorAll("iframe,frame")].find(
            (element) => /** @type {HTMLIFrameElement} */ (element).contentWindow === event.source,
        );
        if (!frame || !event.source) return;
        const element = /** @type {HTMLIFrameElement} */ (frame);
        const rect = element.getBoundingClientRect();
        if (element.offsetWidth <= 0 || element.offsetHeight <= 0) return;
        const sx = rect.width / element.offsetWidth;
        const sy = rect.height / element.offsetHeight;
        const point = {
            x: rect.left + (element.clientLeft + data.x) * sx,
            y: rect.top + (element.clientTop + data.y) * sy,
            ...viewport(),
        };
        const child = /** @type {Window} */ (event.source);
        mapUp(
            point,
            (result) => {
                if (element.isConnected && element.contentWindow === child) {
                    child.postMessage({ type: MAPPED, id: data.id, ...result }, "*");
                }
            },
            data.depth + 1,
        );
    };

    /** @param {OctowrightInteractionPayload} payload */
    const emit = (payload) => {
        if (!record || !eventBinding) return;
        const version = revision;
        const contact = contacts.get(payload.pointer_id);
        const finish = (point) => {
            if (
                destroyed ||
                !record ||
                revision !== version ||
                (contact?.cancelled && payload.phase !== "cancel")
            )
                return;
            if (
                contact &&
                contacts.get(payload.pointer_id) === contact &&
                (payload.phase === "down" || payload.phase === "move")
            ) {
                mappedContacts.set(payload.pointer_id, {
                    x: point.x,
                    y: point.y,
                    width: point.width,
                    height: point.height,
                });
            }
            try {
                Promise.resolve(eventBinding({ ...payload, ...point })).catch(() => {});
            } catch {
                /* Observations never break application input. */
            }
        };
        const known = mappedContacts.get(payload.pointer_id);
        // Cleanup must survive clearing pending relays and disabling recording.
        // The last accepted top-level point is sufficient to end a contact.
        if (payload.phase === "cancel" && known) finish(known);
        else mapUp(payload, finish);
    };
    const build = () => {
        if (root || !document.documentElement) return;
        root = document.createElement("div");
        root.id = ROOT;
        root.setAttribute("aria-hidden", "true");
        root.style.cssText =
            "all:initial!important;position:fixed!important;left:0!important;top:0!important;width:0!important;height:0!important;pointer-events:none!important;z-index:2147483647!important";
        // Closed roots keep feedback labels out of application text locators.
        shadow = root.attachShadow({ mode: "closed" });
        const style = document.createElement("style");
        style.textContent = `
          :host,*{pointer-events:none!important;user-select:none!important}
          [data-phase]{position:fixed;box-sizing:border-box;width:30px;height:30px;
            transform:translate(-50%,-50%);border:3px solid #0284c7;border-radius:50%;
            background:#e0f2fe88;color:#071827;font:bold 11px/24px system-ui;
            text-align:center;white-space:nowrap;text-shadow:0 0 3px white}
          [data-button="2"]{border-style:dashed;border-color:#c026d3}
          [data-button="1"]{border-radius:4px;border-color:#ea580c}
          [data-pointer="touch"]{border-style:dotted;border-color:#059669}
          [data-phase="hold"]{border-width:5px;background:#fbbf2488}
          [data-phase="double"]{border:6px double #7c3aed;width:38px;height:38px}
          [data-phase="up"],[data-phase="double"],[data-phase="wheel"]{animation:ow-interaction-out 420ms ease-out forwards}
          @keyframes ow-interaction-out{to{opacity:0;transform:translate(-50%,-50%) scale(1.6)}}`;
        shadow.append(style);
        document.documentElement.append(root);
    };
    /** @param {OctowrightInteractionPayload} payload */
    const label = (payload) =>
        payload.button === 2
            ? "Right"
            : payload.button === 1
              ? "Middle"
              : payload.pointer_type === "touch"
                ? `Touch ${payload.pointer_id}`
                : payload.pointer_type === "pen"
                  ? "Pen"
                  : "Left";
    const marker = (payload, text) => {
        if (!show) return null;
        build();
        if (!shadow) return null;
        const node = document.createElement("div");
        node.dataset.phase = payload.phase;
        node.dataset.button = String(payload.button);
        node.dataset.pointer = payload.pointer_type;
        node.textContent = text;
        node.style.left = `${payload.x}px`;
        node.style.top = `${payload.y}px`;
        shadow.append(node);
        return node;
    };
    const remove = (node) => {
        if (!node) return;
        const timer = fading.get(node);
        if (timer !== undefined) stop(timer);
        fading.delete(node);
        node.remove();
    };
    const fade = (node, phase) => {
        if (!node) return;
        node.dataset.phase = phase;
        // Bound short-lived markers independently of the active-contact cap.
        if (fading.size >= 32) remove(fading.keys().next().value);
        fading.set(
            node,
            later(() => {
                fading.delete(node);
                node.remove();
            }, 420),
        );
    };
    /** @param {PointerEvent} event @param {OctowrightInteractionPayload['phase']} phase */
    const payloadFor = (event, phase) => {
        if (
            !["mouse", "touch", "pen"].includes(event.pointerType) ||
            ![0, 1, 2].includes(event.button) ||
            !Number.isInteger(event.pointerId) ||
            event.pointerId < 0 ||
            event.pointerId > 2147483647 ||
            !finite(event.clientX) ||
            !finite(event.clientY)
        )
            return null;
        return /** @type {OctowrightInteractionPayload} */ ({
            v: 1,
            phase,
            pointer_type: event.pointerType,
            pointer_id: event.pointerId,
            button: event.button,
            x: event.clientX,
            y: event.clientY,
            ...viewport(),
            at: epoch(),
            shown: show,
        });
    };
    /** @param {PointerEvent} event */
    const down = (event) => {
        if (!event.isTrusted || (!show && !record) || contacts.size >= 16) return;
        const payload = payloadFor(event, "down");
        if (!payload) return;
        if (payload.pointer_type !== "mouse") mouseRelease = null;
        const previous = contacts.get(payload.pointer_id);
        if (previous) {
            stop(previous.hold);
            remove(previous.marker);
            contacts.delete(payload.pointer_id);
        }
        const touchPeers =
            payload.pointer_type === "touch"
                ? [...contacts.values()].filter((item) => item.payload.pointer_type === "touch")
                : [];
        for (const peer of touchPeers) peer.multi = true;
        const contact = {
            payload,
            startX: payload.x,
            startY: payload.y,
            started: now(),
            moved: false,
            multi: touchPeers.length > 0,
            cancelled: false,
            lastMove: -Infinity,
            marker: marker(payload, label(payload)),
            hold: 0,
        };
        if (show)
            contact.hold = later(() => {
                if (!contact.marker || contact.moved) return;
                contact.marker.textContent = "Hold";
                contact.marker.dataset.phase = "hold";
            }, 500);
        contacts.set(payload.pointer_id, contact);
        emit(payload);
    };
    /** @param {PointerEvent} event */
    const move = (event) => {
        const contact = contacts.get(event.pointerId);
        if (!event.isTrusted || !contact || !finite(event.clientX) || !finite(event.clientY)) return;
        const distance = Math.hypot(event.clientX - contact.startX, event.clientY - contact.startY);
        if (!contact.moved && distance < 4) return;
        contact.moved = true;
        stop(contact.hold);
        contact.payload = {
            ...contact.payload,
            x: event.clientX,
            y: event.clientY,
            at: epoch(),
            phase: "move",
        };
        if (contact.marker) {
            contact.marker.textContent = "Drag";
            contact.marker.dataset.phase = "move";
            contact.marker.style.left = `${event.clientX}px`;
            contact.marker.style.top = `${event.clientY}px`;
        }
        if (now() - contact.lastMove >= 50) {
            contact.lastMove = now();
            emit(contact.payload);
        }
    };
    /** @param {PointerEvent} event */
    const up = (event) => {
        const contact = contacts.get(event.pointerId);
        if (!event.isTrusted || !contact) return;
        stop(contact.hold);
        contacts.delete(event.pointerId);
        const cancelled = event.type === "pointercancel" || !finite(event.clientX) || !finite(event.clientY);
        const payload = {
            ...contact.payload,
            phase: /** @type {'up'|'cancel'} */ (cancelled ? "cancel" : "up"),
            at: epoch(),
        };
        if (!cancelled) {
            payload.x = event.clientX;
            payload.y = event.clientY;
        }
        emit(payload);
        mappedContacts.delete(event.pointerId);
        if (cancelled) {
            mouseRelease = null;
            contact.cancelled = true;
            remove(contact.marker);
            lastTap = null;
            return;
        }
        const tap = !contact.moved && !contact.multi && now() - contact.started < 500;
        const double =
            tap &&
            lastTap &&
            now() - lastTap.at <= 300 &&
            lastTap.type === payload.pointer_type &&
            lastTap.button === payload.button &&
            Math.hypot(lastTap.x - payload.x, lastTap.y - payload.y) <= 24;
        if (double && lastTap) {
            remove(lastTap.marker);
            if (contact.marker) contact.marker.textContent = "Double";
            emit({ ...payload, phase: "double" });
            lastTap = null;
        } else {
            lastTap = tap
                ? {
                      at: now(),
                      x: payload.x,
                      y: payload.y,
                      type: payload.pointer_type,
                      button: payload.button,
                      marker: contact.marker,
                  }
                : null;
        }
        if (payload.pointer_type === "mouse") {
            mouseRelease = {
                payload,
                marker: contact.marker,
                previous: mouseRelease?.marker ?? null,
                doubled: Boolean(double),
            };
        }
        fade(contact.marker, double ? "double" : "up");
    };
    /** @param {MouseEvent} event */
    const nativeDouble = (event) => {
        const released = mouseRelease;
        if (!event.isTrusted || !released || released.doubled || event.button !== released.payload.button)
            return;
        // Native mouse classification is authoritative outside the local tap
        // heuristic. The release flag deduplicates the quick-click path.
        released.doubled = true;
        remove(released.previous);
        if (released.marker) {
            released.marker.textContent = "Double";
            released.marker.dataset.phase = "double";
        }
        lastTap = null;
        emit({ ...released.payload, phase: "double", at: epoch() });
    };
    /** @param {WheelEvent} event */
    const wheel = (event) => {
        if (
            !event.isTrusted ||
            (!show && !record) ||
            ![event.clientX, event.clientY, event.deltaX, event.deltaY].every(finite)
        )
            return;
        const payload = /** @type {OctowrightInteractionPayload} */ ({
            v: 1,
            phase: "wheel",
            pointer_type: "mouse",
            pointer_id: 0,
            button: 0,
            x: event.clientX,
            y: event.clientY,
            ...viewport(),
            at: epoch(),
            shown: show,
            dx: event.deltaX,
            dy: event.deltaY,
        });
        const arrow =
            Math.abs(event.deltaY) >= Math.abs(event.deltaX)
                ? event.deltaY >= 0
                    ? "↓"
                    : "↑"
                : event.deltaX >= 0
                  ? "→"
                  : "←";
        fade(marker(payload, `Wheel ${arrow}`), "wheel");
        emit(payload);
    };
    const cancelContact = (id, contact) => {
        emit({ ...contact.payload, phase: "cancel", at: epoch() });
        contact.cancelled = true;
        stop(contact.hold);
        remove(contact.marker);
        contacts.delete(id);
        mappedContacts.delete(id);
        mouseRelease = null;
        lastTap = null;
    };
    /** @param {PointerEvent} event */
    const leave = (event) => {
        if (!event.isTrusted || event.relatedTarget != null) return;
        const contact = contacts.get(event.pointerId);
        if (contact) cancelContact(event.pointerId, contact);
    };
    const reset = () => {
        for (const [id, contact] of contacts) cancelContact(id, contact);
        mappedContacts.clear();
        for (const timer of timers) window.clearTimeout(timer);
        timers.clear();
        pending.clear();
        fading.clear();
        mouseRelease = null;
        lastTap = null;
        root?.remove();
        root = null;
        shadow = null;
    };
    /** @param {{show:boolean,record:boolean}} options */
    const configure = (options) => {
        if (destroyed || !options || typeof options.show !== "boolean" || typeof options.record !== "boolean")
            return;
        configured = true;
        if (options.show === show && options.record === record) return;
        reset();
        revision++;
        show = options.show;
        record = options.record;
    };
    const destroy = () => {
        if (destroyed) return;
        reset();
        destroyed = true;
        for (const [type, handler] of listeners) window.removeEventListener(type, handler, true);
        delete window.__octowrightInteractions;
    };
    const listen = (type, handler) => {
        window.addEventListener(type, handler, { capture: true, passive: true });
        listeners.push([type, handler]);
    };
    listen("pointerdown", down);
    listen("pointermove", move);
    listen("pointerup", up);
    listen("pointercancel", up);
    listen("pointerout", leave);
    listen("wheel", wheel);
    listen("dblclick", nativeDouble);
    listen("blur", (event) => {
        // Capture also sees ordinary element focus transitions within this page.
        if (event.target === window || event.target === document) reset();
    });
    listen("pagehide", reset);
    listen("message", relay);
    const controller = { configure, destroy, ready: Promise.resolve() };
    window.__octowrightInteractions = controller;
    controller.ready = (async () => {
        try {
            const options = await configBinding?.({
                time_origin: performance.timeOrigin,
                width: window.innerWidth,
                height: window.innerHeight,
                dpr: window.devicePixelRatio,
            });
            if (!configured && options) configure(options);
        } catch {
            /* A missing/closed host leaves this document inert. */
        }
    })();
})();
