// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

async function run(source, scenario) {
    const listeners = new Set();
    const frames = new Map();
    const timers = new Map();
    const removed = [];
    const cancelled = [];
    let nextId = 0;
    let result = "pending";
    class Element {
        scrollLeft = 0;
        scrollTop = 0;
    }
    const target = new Element();
    const ancestor = new Element();
    const window = {
        scrollX: 0,
        scrollY: 0,
        addEventListener(type, callback, options) {
            assert.equal(type, "wheel");
            assert.equal(options.capture, true, "observe during capture");
            assert.equal(options.passive, true, "observer must not prevent native scrolling");
            listeners.add(callback);
        },
        removeEventListener(type, callback, capture) {
            assert.equal(type, "wheel");
            assert.equal(capture, true);
            removed.push(callback);
            listeners.delete(callback);
        },
    };
    const context = {
        window,
        Element,
        requestAnimationFrame(callback) {
            const id = ++nextId;
            frames.set(id, callback);
            return id;
        },
        cancelAnimationFrame(id) {
            cancelled.push(id);
            frames.delete(id);
        },
        setTimeout(callback, delay) {
            const id = ++nextId;
            timers.set(id, { callback, delay });
            return id;
        },
        clearTimeout(id) { timers.delete(id); },
    };
    // Support either window-qualified or unqualified browser primitives.
    Object.assign(window, context);
    const state = vm.runInNewContext(`(${source})()`, context, { timeout: 1000 });
    state.done.then(value => { result = value; });
    const flush = async () => { await Promise.resolve(); await Promise.resolve(); };
    const noDeadline = () => {
        assert.equal(timers.size, 0, "settling deadline belongs to the host, after native dispatch");
    };
    const frame = async () => {
        noDeadline();
        assert.equal(frames.size, 1, "observer must schedule exactly one next frame");
        const callbacks = [...frames.values()];
        frames.clear();
        for (const callback of callbacks) callback(0);
        await flush();
        noDeadline();
    };
    const wheel = (prevented = false) => {
        assert.equal(listeners.size, 1, "listener must already be armed at wheel dispatch");
        const event = {
            defaultPrevented: prevented,
            // Real composed paths include non-elements such as window/document.
            composedPath: () => [target, {}, ancestor, window],
        };
        for (const callback of [...listeners]) callback(event);
        noDeadline();
    };
    const clean = () => {
        assert.equal(listeners.size, 0, "wheel listener leaked");
        assert.equal(frames.size, 0, "animation frame leaked");
        noDeadline();
        assert.ok(removed.length > 0, "listener removal was never attempted");
    };
    assert.equal(listeners.size, 0, "allocation must not register an unowned listener");
    assert.equal(frames.size, 0, "allocation must not schedule an unowned frame");
    noDeadline();
    if (scenario === "cancel_before_arm") {
        state.cancel();
        state.arm();
        await flush();
        assert.equal(result, false);
        assert.equal(listeners.size, 0, "late arming must not revive a cancelled observer");
        assert.equal(frames.size, 0);
        noDeadline();
        return;
    }
    state.arm();
    noDeadline();
    assert.equal(listeners.size, 1, "arming must register observation synchronously");
    assert.equal(frames.size, 0, "do not converge before observing wheel input");
    if (scenario === "no_page_deadline") {
        noDeadline();
        wheel();
        await frame();
        assert.equal(result, "pending");
        noDeadline();
        state.cancel();
        await flush();
        clean();
        return;
    }
    if (scenario === "wait_for_wheel") {
        await flush();
        assert.equal(result, "pending");
        assert.equal(frames.size, 0);
        state.cancel();
        await flush();
        assert.equal(result, false, "unobserved input cannot count as settled");
        clean();
        return;
    }
    if (scenario.startsWith("cancel_")) {
        if (scenario !== "cancel_before_wheel") wheel();
        if (scenario === "cancel_after_frame") await frame();
        noDeadline();
        const pendingFrames = [...frames.keys()];
        const registered = [...listeners];
        state.cancel();
        await flush();
        assert.equal(result, false, "cancellation is distinct from stability");
        clean();
        for (const id of pendingFrames) assert.ok(cancelled.includes(id), "pending RAF was not cancelled");
        for (const callback of registered) assert.ok(removed.includes(callback), "wrong listener removed");
        state.cancel();
        await flush();
        assert.equal(result, false, "repeated cancellation must preserve the result");
        clean();
        return;
    }
    wheel(scenario === "prevented_wheel");
    assert.equal(result, "pending", "dispatch itself does not prove rendering has settled");
    await frame();
    assert.equal(result, "pending", "first frame establishes the baseline");
    await frame();
    assert.equal(result, "pending", "one stable interval is insufficient");
    if (!["prevented_wheel", "boundary_wheel"].includes(scenario)) {
        const [owner, axis] = scenario.split("_");
        const node = { window, target, ancestor }[owner];
        const property = owner === "window" ? (axis === "x" ? "scrollX" : "scrollY")
            : (axis === "x" ? "scrollLeft" : "scrollTop");
        // Movement after a stable interval must reset the consecutive counter.
        for (const value of [10, 20, 30]) {
            node[property] = value;
            await frame();
            assert.equal(result, "pending", `${scenario} movement must prevent convergence`);
        }
        await frame();
        assert.equal(result, "pending", "movement must reset the stability counter");
    }
    await frame();
    assert.equal(result, true, "two consecutive stable intervals must settle without a deadline");
    clean();
    state.cancel();
    await flush();
    assert.equal(result, true, "cleanup after convergence must preserve success");
    clean();
}

const { source, scenario } = JSON.parse(fs.readFileSync(0, "utf8"));
run(source, scenario).catch(error => { console.error(error); process.exitCode = 1; });
