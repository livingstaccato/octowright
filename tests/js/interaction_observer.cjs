// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const { scheduler } = require("./interaction_scheduler.cjs");
const asset = process.argv[2];
// An empty observer makes the missing feature an assertion failure, not an I/O error.
const source = fs.existsSync(asset) ? fs.readFileSync(asset, "utf8") : "";
const cases = {
    async hold() {
        const s = scheduler(source);
        assert.ok(s.window.__octowrightInteractions, "document controller must be installed");
        await s.window.__octowrightInteractions.ready;
        s.fire("pointerdown");
        assert.equal(s.markers()[0].textContent, "Left");
        await s.tick(499);
        assert.equal(s.markers()[0].textContent, "Left");
        await s.tick(1);
        assert.equal(s.markers()[0].textContent, "Hold");
        s.fire("pointerup");
        await s.tick(450);
        assert.equal(s.markers().length, 0);
        assert.deepEqual(
            s.events.map((event) => event.phase),
            ["down", "up"],
        );
        s.fire("pointerdown", { pointerType: "touch", pointerId: 7 });
        await s.tick(500);
        assert.equal(s.markers()[0].textContent, "Hold");
        s.fire("pointerup", { pointerType: "touch", pointerId: 7 });
        await s.tick(450);
        assert.equal(s.markers().length, 0);
    },
    async buttons_contacts() {
        const s = scheduler(source);
        assert.ok(s.window.__octowrightInteractions, "document controller must be installed");
        await s.window.__octowrightInteractions.ready;
        for (const [pointerId, pointerType, button] of [
            [1, "mouse", 2],
            [2, "touch", 0],
            [3, "pen", 1],
        ]) {
            s.fire("pointerdown", { pointerId, pointerType, button });
        }
        assert.deepEqual(
            s.markers().map((node) => node.textContent),
            ["Right", "Touch 2", "Middle"],
        );
        assert.deepEqual(
            s.events.map((event) => [event.pointer_type, event.button]),
            [
                ["mouse", 2],
                ["touch", 0],
                ["pen", 1],
            ],
        );
        for (let id = 4; id <= 30; id++) s.fire("pointerdown", { pointerId: id, pointerType: "touch" });
        assert.equal(s.markers().length, 16);
        assert.equal(s.events.length, 16);
    },
    async double() {
        const s = scheduler(source);
        assert.ok(s.window.__octowrightInteractions, "document controller must be installed");
        await s.window.__octowrightInteractions.ready;
        for (let count = 0; count < 2; count++) {
            s.fire("pointerdown");
            s.fire("pointerup");
            await s.tick(100);
        }
        s.fire("dblclick", { detail: 2 });
        assert.equal(s.events.filter((event) => event.phase === "double").length, 1);
        assert.equal(s.markers().length, 1);
        assert.equal(s.markers()[0].textContent, "Double");
        assert.equal(s.markers()[0].dataset.phase, "double");
    },
    async drag_wheel() {
        const s = scheduler(source);
        assert.ok(s.window.__octowrightInteractions, "document controller must be installed");
        await s.window.__octowrightInteractions.ready;
        s.fire("pointerdown");
        for (let count = 0; count < 100; count++) {
            s.fire("pointermove", { clientX: 50 + count });
            await s.tick(1);
        }
        assert.equal(s.events.filter((event) => event.phase === "move").length, 2);
        assert.equal(s.markers()[0].textContent, "Drag");
        await s.tick(500);
        assert.equal(s.markers()[0].textContent, "Drag");
        s.fire("pointerup", { clientX: 149 });
        s.fire("wheel", { deltaX: -1, deltaY: 20 });
        assert.equal(s.events.at(-1).phase, "wheel");
        assert.equal(s.events.at(-1).dy, 20);
        assert.ok(s.markers().some((node) => node.textContent.includes("↓")));
    },
    async cleanup() {
        for (const reason of ["blur", "pagehide", "pointercancel", "pointerout", "disable", "destroy"]) {
            const s = scheduler(source);
            assert.ok(s.window.__octowrightInteractions, "document controller must be installed");
            await s.window.__octowrightInteractions.ready;
            s.fire("pointerdown");
            if (reason === "disable")
                s.window.__octowrightInteractions.configure({ show: false, record: false });
            else if (reason === "destroy") s.window.__octowrightInteractions.destroy();
            else s.fire(reason);
            await s.tick(1000);
            assert.equal(s.markers().length, 0, reason);
            assert.equal(s.timers.size, 0, reason);
            assert.equal(s.events.at(-1).phase, "cancel", reason);
        }
    },
    async modes_repeat() {
        for (const options of [
            { show: true, record: false },
            { show: false, record: true },
            { show: false, record: false },
        ]) {
            const s = scheduler(source, options);
            assert.ok(s.window.__octowrightInteractions, "document controller must be installed");
            await s.window.__octowrightInteractions.ready;
            const controller = s.window.__octowrightInteractions;
            s.install();
            assert.equal(s.window.__octowrightInteractions, controller);
            s.fire("pointerdown");
            assert.equal(s.events.length, options.record ? 1 : 0);
            assert.equal(s.markers().length, options.show ? 1 : 0);
            controller.configure({ show: true, record: true });
            s.fire("pointerdown", { pointerId: 9 });
            assert.equal(
                s.events.filter((event) => event.phase === "down" && event.pointer_id === 9).length,
                1,
            );
            controller.destroy();
            s.fire("pointerdown", { pointerId: 10 });
            assert.equal(s.events.filter((event) => event.pointer_id === 10).length, 0);
        }
    },
    async frame_relay() {
        const s = scheduler(source);
        assert.ok(s.window.__octowrightInteractions, "document controller must be installed");
        await s.window.__octowrightInteractions.ready;
        const messages = [];
        const parent = { postMessage: (data) => messages.push(data) };
        s.window.parent = parent;
        s.fire("pointerdown");
        assert.equal(messages.length, 1);
        assert.equal(s.events.length, 0);
        const response = {
            type: "octowright:interaction:mapped",
            id: messages[0].id,
            x: 120,
            y: 130,
            width: 1200,
            height: 900,
        };
        s.fire("message", { source: {}, data: response });
        s.fire("message", { source: parent, data: { ...response, x: NaN } });
        assert.equal(s.events.length, 0);
        s.fire("message", { source: parent, data: response });
        assert.equal(s.events.length, 1);
        assert.equal(s.events[0].x, 120);
        assert.equal(s.events[0].width, 1200);
        assert.equal(s.markers()[0].style.left, "20px");
        s.fire("blur");
        assert.equal(s.events.at(-1).phase, "cancel");
        assert.equal(s.events.at(-1).x, 120);
        assert.equal(s.timers.size, 0);
        s.fire("message", { source: parent, data: response });
        assert.equal(s.events.length, 2);
    },
    async late_replies() {
        for (const action of ["disable", "destroy", "blur"]) {
            const s = scheduler(source);
            await s.window.__octowrightInteractions.ready;
            const messages = [];
            const parent = { postMessage: (data) => messages.push(data) };
            s.window.parent = parent;
            s.fire("pointerdown");
            assert.equal(messages.length, 1);
            if (action === "disable")
                s.window.__octowrightInteractions.configure({ show: true, record: false });
            else if (action === "destroy") s.window.__octowrightInteractions.destroy();
            else s.fire("blur");
            s.fire("message", {
                source: parent,
                data: {
                    type: "octowright:interaction:mapped",
                    id: messages[0].id,
                    x: 1,
                    y: 2,
                    width: 800,
                    height: 600,
                },
            });
            await s.tick(1000);
            assert.equal(s.events.length, 0, action);
            assert.equal(s.timers.size, 0, action);
        }
    },
    async reused_contact() {
        const s = scheduler(source);
        await s.window.__octowrightInteractions.ready;
        const messages = [];
        const parent = { postMessage: (data) => messages.push(data) };
        s.window.parent = parent;
        const answer = (message) =>
            s.fire("message", {
                source: parent,
                data: {
                    type: "octowright:interaction:mapped",
                    id: message.id,
                    x: 120,
                    y: 130,
                    width: 800,
                    height: 600,
                },
            });
        s.fire("pointerdown");
        s.fire("pointerup");
        answer(messages[0]);
        answer(messages[1]);
        assert.equal(s.events.length, 2);
        s.fire("pointerdown");
        s.fire("blur");
        // No response accepted the new down, so the old contact's geometry
        // must not produce a stray cancellation for a reused pointer ID.
        assert.equal(s.events.length, 2);
    },
    async simultaneous_touch_not_double() {
        const s = scheduler(source);
        await s.window.__octowrightInteractions.ready;
        s.fire("pointerdown", { pointerType: "touch", pointerId: 1 });
        s.fire("pointerdown", { pointerType: "touch", pointerId: 2, clientX: 22 });
        s.fire("pointerup", { pointerType: "touch", pointerId: 1 });
        s.fire("pointerup", { pointerType: "touch", pointerId: 2, clientX: 22 });
        assert.equal(s.events.filter((event) => event.phase === "double").length, 0);
        await s.tick(500);
        for (const pointerId of [3, 4]) {
            s.fire("pointerdown", { pointerType: "touch", pointerId });
            s.fire("pointerup", { pointerType: "touch", pointerId });
            await s.tick(100);
        }
        assert.equal(s.events.filter((event) => event.phase === "double").length, 1);
    },
    async noop_configure_frame() {
        const s = scheduler(source);
        await s.window.__octowrightInteractions.ready;
        const messages = [];
        const parent = { postMessage: (data) => messages.push(data) };
        s.window.parent = parent;
        const answer = (message) =>
            s.fire("message", {
                source: parent,
                data: {
                    type: "octowright:interaction:mapped",
                    id: message.id,
                    x: 120,
                    y: 130,
                    width: 800,
                    height: 600,
                },
            });
        s.fire("pointerdown");
        s.window.__octowrightInteractions.configure({ show: true, record: true });
        answer(messages[0]);
        s.fire("pointerup");
        answer(messages[1]);
        assert.deepEqual(
            s.events.map((event) => event.phase),
            ["down", "up"],
        );
    },
    async noop_before_ready() {
        const s = scheduler(source);
        s.window.__octowrightInteractions.configure({ show: false, record: false });
        await s.window.__octowrightInteractions.ready;
        s.fire("pointerdown");
        assert.equal(s.events.length, 0);
        assert.equal(s.markers().length, 0);
    },
    async native_slow_double() {
        const s = scheduler(source);
        await s.window.__octowrightInteractions.ready;
        for (let click = 0; click < 2; click++) {
            s.fire("pointerdown");
            await s.tick(200);
            s.fire("pointerup");
            if (click === 0) await s.tick(200);
        }
        assert.equal(s.events.filter((event) => event.phase === "double").length, 0);
        s.fire("dblclick", { detail: 2, isTrusted: false });
        assert.equal(s.events.filter((event) => event.phase === "double").length, 0);
        s.fire("dblclick", { detail: 2 });
        s.fire("dblclick", { detail: 2 });
        assert.deepEqual(
            s.events.map((event) => event.phase),
            ["down", "up", "down", "up", "double"],
        );
        assert.equal(s.markers().length, 1);
        assert.equal(s.markers()[0].textContent, "Double");
    },
    async malformed() {
        const s = scheduler(source);
        assert.ok(s.window.__octowrightInteractions, "document controller must be installed");
        await s.window.__octowrightInteractions.ready;
        for (const data of [
            null,
            {},
            { type: "octowright:interaction:map", x: Infinity },
            { type: "octowright:interaction:mapped", id: "unknown", x: 1, y: 2 },
        ]) {
            s.fire("message", { data, source: {} });
        }
        s.fire("pointerdown", { clientX: NaN });
        s.fire("pointerdown", { isTrusted: false });
        assert.equal(s.events.length, 0);
        assert.equal(s.markers().length, 0);
    },
};
(async () => {
    await cases[process.argv[3]]();
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
