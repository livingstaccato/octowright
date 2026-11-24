// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

const vm = require("node:vm");
const { randomFillSync } = require("node:crypto");

class Element {
    constructor(tag = "div") {
        this.tagName = tag.toUpperCase();
        this.style = {};
        this.children = [];
        this.dataset = {};
        this.textContent = "";
        this.id = "";
    }
    append(...nodes) {
        for (const node of nodes) {
            node.parentNode = this;
            this.children.push(node);
        }
    }
    appendChild(node) {
        this.append(node);
        return node;
    }
    remove() {
        if (this.parentNode) {
            this.parentNode.children = this.parentNode.children.filter((node) => node !== this);
        }
    }
    attachShadow() {
        this.shadowRoot = new Element("shadow");
        return this.shadowRoot;
    }
    setAttribute(name, value) {
        this[name] = value;
    }
}

function scheduler(source, options = { show: true, record: true }) {
    let now = 0;
    let nextTimer = 0;
    const timers = new Map();
    const listeners = new Map();
    const events = [];
    const root = new Element("html");
    const walk = (node) => [
        node,
        ...node.children.flatMap(walk),
        ...(node.shadowRoot ? walk(node.shadowRoot) : []),
    ];
    const document = {
        documentElement: root,
        body: root,
        createElement: (tag) => new Element(tag),
        getElementById: (id) => walk(root).find((node) => node.id === id) || null,
        querySelectorAll: () => [],
    };
    const window = {
        document,
        innerWidth: 800,
        innerHeight: 600,
        performance: { timeOrigin: 1700000000000, now: () => now },
        crypto: { getRandomValues: randomFillSync },
        setTimeout: (callback, delay) => {
            const id = ++nextTimer;
            timers.set(id, { callback, at: now + delay });
            return id;
        },
        clearTimeout: (id) => timers.delete(id),
        addEventListener: (type, callback) => {
            if (!listeners.has(type)) listeners.set(type, new Set());
            listeners.get(type).add(callback);
        },
        removeEventListener: (type, callback) => listeners.get(type)?.delete(callback),
        __octowrightInteractionConfig: async () => options,
        __octowrightInteractionEvent: async (payload) => events.push(payload),
    };
    window.top = window;
    window.parent = window;
    window.self = window;
    window.window = window;
    const context = vm.createContext(window);
    const install = () => vm.runInContext(source, context);
    const flush = async () => {
        await Promise.resolve();
        await Promise.resolve();
    };
    const tick = async (ms) => {
        const end = now + ms;
        while (true) {
            const due = [...timers]
                .filter(([, timer]) => timer.at <= end)
                .sort((a, b) => a[1].at - b[1].at)[0];
            if (!due) break;
            now = due[1].at;
            timers.delete(due[0]);
            due[1].callback();
            await flush();
        }
        now = end;
        await flush();
    };
    const fire = (type, values = {}) => {
        const event = {
            type,
            target: vm.runInContext("window", context),
            isTrusted: true,
            pointerId: 1,
            pointerType: "mouse",
            button: 0,
            clientX: 20,
            clientY: 30,
            deltaX: 0,
            deltaY: 0,
            detail: 1,
            ...values,
        };
        for (const callback of [...(listeners.get(type) || [])]) callback(event);
    };
    install();
    return {
        window,
        document,
        events,
        tick,
        fire,
        flush,
        install,
        timers,
        markers: () => walk(root).filter((node) => node.dataset.phase),
    };
}
module.exports = { scheduler };
