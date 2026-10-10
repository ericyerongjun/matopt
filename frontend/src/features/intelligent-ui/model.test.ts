import assert from "node:assert/strict";
import { test } from "node:test";
import type { UISurface } from "../../types/ui.ts";
import { actionValues, declaredStateUpdate, initialSurfaceState, isSurface, resetSurfaceState, resolveBindings, surfaceExport } from "./model.ts";
import { sandboxOrigin, validateFrameMessage } from "./bridge.ts";

const surface: UISurface = { version: 1, id: "s1", revision: 1, kind: "native", title: "Taylor explorer", fallback: "A polynomial approximation.", status: "committed", state: { degree: 5, label: "Current degree" }, actions: [],
    nodes: [{ id: "degree", type: "slider", props: { label: "Degree", stateKey: "degree", min: 1, max: 12, value: 3 } }] };

test("surface boundary rejects unknown versions, duplicate nodes, nonfinite values and unsafe state keys", () => {
    assert.equal(isSurface(surface), true);
    assert.equal(isSurface({ ...surface, version: 2 }), false);
    assert.equal(isSurface({ ...surface, nodes: [surface.nodes[0], surface.nodes[0]] }), false);
    assert.equal(isSurface({ ...surface, state: { degree: Infinity } }), false);
    assert.equal(isSurface({ ...surface, actions: [null] }), false);
    assert.equal(isSurface({ ...surface, nodes: [{ ...surface.nodes[0], props: { stateKey: "__proto__" } }] }), false);
    assert.equal(isSurface({ ...surface, state: JSON.parse('{"__proto__":{"polluted":true}}') }), false);
});

test("valid names that overlap Object.prototype remain data without inheriting values", () => {
    const named = { ...surface, id: "toString", state: {}, nodes: [{ id: "valueOf", type: "number" as const, props: { label: "Value", stateKey: "toString", value: 4 } }] };
    assert.equal(isSurface(named), true); assert.deepEqual(initialSurfaceState(named), { toString: 4 });
    assert.equal(resolveBindings({ $state: "toString" }, {}), null);
});

test("control defaults, local bindings and exports preserve values without evaluating expressions", () => {
    assert.equal(initialSurfaceState(surface).degree, 5);
    assert.equal(resetSurfaceState(surface).degree, 3);
    assert.deepEqual(resolveBindings({ values: [{ $state: "degree" }], literal: "window.alert(1)" }, surface.state), { values: [5], literal: "window.alert(1)" });
    const exported = JSON.parse(surfaceExport(surface, { degree: 7 }));
    assert.equal(exported.state.degree, 7); assert.equal(exported.fallback, surface.fallback);
});

test("reset defaults follow the current control schema and discard removed fields", () => {
    const revised: UISurface = { ...surface, state: { alpha: 2 }, nodes: [{ id: "alpha", type: "number", props: { label: "Alpha", stateKey: "alpha", value: 1 } }] };
    assert.deepEqual(resetSurfaceState(revised, { degree: 3, removed: true }), { alpha: 1 });
});

test("action payloads contain only the declared input fields", () => {
    const action = { id: "recompute", label: "Compute", handler_version: 1, input_schema: { type: "object", properties: { degree: { type: "number" } }, additionalProperties: false } };
    assert.deepEqual(actionValues(action, surface.state), { degree: 5 });
    assert.deepEqual(actionValues({ ...action, input_schema: { ...action.input_schema, additionalProperties: true } }, surface.state), surface.state);
});

test("mini-app state is bounded and limited to declared keys", () => {
    assert.deepEqual(declaredStateUpdate({ degree: 6 }, surface.state), { degree: 6, label: "Current degree" });
    assert.equal(declaredStateUpdate({ execute: "import os" }, surface.state), null);
    assert.equal(declaredStateUpdate({ label: "x".repeat(65_000) }, surface.state), null);
    assert.equal(declaredStateUpdate({ degree: NaN }, surface.state), null);
});

test("bridge rejects forged channels, revisions and undeclared actions; resize values are clamped", () => {
    const identity = { surfaceId: "s1", channel: "secret" };
    const envelope = { version: 1, ...identity };
    assert.equal(validateFrameMessage({ ...envelope, channel: "wrong", type: "matopt:state", state: { degree: 2 } }, identity, surface.state), null);
    assert.equal(validateFrameMessage({ ...envelope, version: 2, type: "matopt:ready" }, identity, surface.state), null);
    assert.equal(validateFrameMessage({ ...envelope, type: "matopt:action", action: "python" }, identity, surface.state), null);
    assert.equal(validateFrameMessage({ ...envelope, type: "matopt:state", state: { url: "https://example.com" } }, identity, surface.state), null);
    assert.deepEqual(validateFrameMessage({ ...envelope, type: "matopt:resize", height: 9000 }, identity, surface.state), { type: "matopt:resize", height: 1200 });
    assert.deepEqual(validateFrameMessage({ ...envelope, type: "matopt:state", state: { degree: 2 } }, identity, surface.state), { type: "matopt:state", state: { degree: 2, label: "Current degree" } });
});

test("runtime defaults to a distinct origin and rejects same-origin or nonweb configurations", () => {
    const host = { origin: "http://127.0.0.1:5173", hostname: "127.0.0.1", protocol: "http:", port: "5173" };
    assert.equal(sandboxOrigin(host), "http://127.0.0.1:5174");
    assert.equal(sandboxOrigin(host, host.origin), null);
    assert.equal(sandboxOrigin(host, "javascript:alert(1)"), null);
    assert.equal(sandboxOrigin(host, "https://preview.example.com/"), "https://preview.example.com");
});
