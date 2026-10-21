import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { JSDOM } from "jsdom";

// jsdom's internal dispatcher is the only way to produce a trusted (user) click.
const require = createRequire(import.meta.url);
const { fireAnEvent } = require("jsdom/lib/jsdom/living/helpers/events.js");
const idlUtils = require("jsdom/lib/generated/idl/utils.js");
const MouseEvent = require("jsdom/lib/generated/idl/MouseEvent.js");

// The MCP Apps view served by the backend at ui://matopt/mini-app-viewer.html.
const VIEWER = readFileSync(new URL("../../backend/app/ui/mcp_app_viewer.html", import.meta.url), "utf8");
const APP = `<!doctype html><html><head><style>p{margin:0}</style></head><body><p id="out"></p>
<script>const draw=(s)=>{document.getElementById("out").textContent=JSON.stringify(s)+" "+matopt.getTheme()};
matopt.onStateChange(draw);draw(matopt.getState());window.bump=(n=5)=>matopt.setState({frequency:n,extra:1});</script></body></html>`;
const ALL = { serverTools: {}, message: { text: {} }, updateModelContext: { text: {} } };
const SURFACE = { conversation_id: "c1", id: "sine-wave", revision: 1, title: "Sine wave", fallback: "Frequency 2.", html: APP,
  state: { frequency: 2 }, computed_state: { frequency: 2 }, token: "secret-token",
  actions: [{ id: "recompute", label: "Recompute", input_schema: { type: "object", properties: { frequency: { type: "integer" } } } }] };

const tick = (ms = 30) => new Promise((resolve) => setTimeout(resolve, ms));

async function mount() {
  const host = new JSDOM("").window;
  const sent = [];
  host.postMessage = (message) => sent.push(JSON.parse(JSON.stringify(message)));  // structured clone across realms
  let view;
  const shadows = [];
  new JSDOM(VIEWER, { runScripts: "dangerously", pretendToBeVisual: true, beforeParse(window) {
    view = window;
    Object.defineProperty(window, "parent", { value: host, configurable: true });
    window.ResizeObserver = class { observe() {} };
    const attach = window.Element.prototype.attachShadow;
    window.Element.prototype.attachShadow = function (init) { const root = attach.call(this, init); shadows.push(root); return root; };
  } });
  const deliver = async (data, source = host) => { view.dispatchEvent(new view.MessageEvent("message", { data, source })); await tick(); };
  const reply = (id, result) => deliver({ jsonrpc: "2.0", id, result });
  const buttons = () => [...(shadows.at(-1)?.querySelectorAll("button") || [])];
  const notice = () => shadows.at(-1)?.querySelector("[role=status]")?.textContent;
  const about = () => shadows.at(-1)?.querySelector("details p")?.textContent;
  const userClick = (element) => fireAnEvent("click", idlUtils.implForWrapper(element), MouseEvent, { bubbles: true });
  const out = () => view.document.getElementById("out")?.textContent;
  const calls = (name) => sent.filter((m) => m.method === "tools/call" && m.params.name === name);
  await tick();
  return { view, sent, deliver, reply, buttons, notice, about, userClick, out, calls };
}

async function start(capabilities = {}, surface = SURFACE, theme = "dark") {
  const app = await mount();
  await app.reply(1, { protocolVersion: "2026-01-26", hostCapabilities: capabilities, hostContext: { theme } });
  await app.deliver(result(surface));
  return app;
}

const result = (surface, extra = {}) => ({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: {
  content: [{ type: "text", text: "A readable fallback." }], structuredContent: { surface_id: "sine-wave" },
  _meta: surface ? { "matopt/surface": surface } : undefined, ...extra } });

test("viewer completes the MCP Apps handshake and adopts host theme and style variables", async () => {
  const { view, sent, deliver, reply } = await mount();
  assert.deepEqual(sent[0], { jsonrpc: "2.0", id: 1, method: "ui/initialize", params: {
    protocolVersion: "2026-01-26", appInfo: { name: "matopt-mini-app-viewer", version: "1" }, appCapabilities: {} } });
  await reply(1, { protocolVersion: "2026-01-26", hostContext: { theme: "dark", styles: { variables: { "--color-background-primary": "#101010", "--font-sans": "Inter" } } } });
  assert.equal(sent[1].method, "ui/notifications/initialized");
  const root = view.document.documentElement;
  assert.equal(root.dataset.theme, "dark");
  assert.equal(root.style.getPropertyValue("--matopt-bg"), "#101010");
  assert.equal(root.style.getPropertyValue("--matopt-font"), "Inter");
  await deliver({ jsonrpc: "2.0", method: "ui/notifications/host-context-changed", params: { theme: "light" } });
  assert.equal(root.dataset.theme, "light");
});

test("tool result renders the saved mini-app with only its declared state", async () => {
  const { view, sent, deliver, out, buttons } = await start();
  assert.equal(out(), '{"frequency":2} dark');
  view.bump();
  assert.equal(out(), '{"frequency":5} dark');
  assert.ok(sent.some((m) => m.method === "ui/notifications/size-changed" && m.params.height >= 160));
  // Hosts without server tools or messages get no viewer controls and no saves.
  await tick(450);
  assert.equal(buttons().length, 0);
  assert.ok(!sent.some((m) => m.method === "tools/call"));
  // Later results never replace the mounted app.
  await deliver(result({ ...SURFACE, html: "<p id=out>replaced</p>" }));
  assert.equal(out(), '{"frequency":5} dark');
});

test("control edits save debounced with the surface token and rebase on conflicts", async () => {
  const { view, sent, reply, calls } = await start(ALL);
  view.bump(3); view.bump(4);
  await tick(450);
  const [save] = calls("save_ui_state");
  assert.deepEqual(save.params.arguments, { conversation_id: "c1", surface_id: "sine-wave", token: "secret-token", revision: 1, state: { frequency: 4 } });
  await reply(save.id, { isError: true, content: [{ type: "text", text: "This UI changed." }], structuredContent: { code: "UI_CONFLICT", revision: 3, state: { frequency: 2 } } });
  const retry = calls("save_ui_state")[1];
  assert.equal(retry.params.arguments.revision, 3);
  assert.deepEqual(retry.params.arguments.state, { frequency: 4 });
  await reply(retry.id, { structuredContent: { surface_id: "sine-wave", revision: 4, state: { frequency: 4 } } });
  const context = sent.filter((m) => m.method === "ui/update-model-context").at(-1);
  assert.deepEqual(context.params.structuredContent, { surface_id: "sine-wave", revision: 4, state: { frequency: 4 }, computed_state: { frequency: 2 } });
});

test("only a user click runs the host-owned action and the result updates the app", async () => {
  const { view, sent, reply, buttons, notice, about, userClick, out, calls } = await start(ALL);
  assert.deepEqual(buttons().map((b) => b.textContent), ["Recompute", "Explain"]);
  assert.equal(about(), "Frequency 2.");
  view.bump(6);
  assert.match(notice(), /Recompute to update/);
  buttons()[0].click();  // scripted clicks, as generated code could issue, are ignored
  await tick();
  assert.equal(calls("run_ui_action").length, 0);
  userClick(buttons()[0]);
  await tick();
  // Pending edits are saved before computing, then the computation uses the new revision.
  const [save] = calls("save_ui_state");
  await reply(save.id, { structuredContent: { revision: 2 } });
  const [run] = calls("run_ui_action");
  assert.equal(run.params.arguments.revision, 2);
  assert.equal(run.params.arguments.token, "secret-token");
  assert.deepEqual(run.params.arguments.values, { frequency: 6 });
  assert.match(run.params.arguments.idempotency_key, /^[0-9a-f-]{32,36}$/);
  assert.ok(buttons().every((b) => b.disabled));
  await reply(run.id, { content: [{ type: "text", text: "Frequency 6." }], structuredContent: { status: "complete", revision: 3 },
    _meta: { "matopt/surface": { ...SURFACE, revision: 3, fallback: "Frequency 6.", state: { frequency: 6 }, computed_state: { frequency: 6 }, token: undefined } } });
  assert.equal(out(), '{"frequency":6} dark');
  assert.equal(notice(), "");
  assert.equal(about(), "Frequency 6.");
  assert.ok(buttons().every((b) => !b.disabled));
  userClick(buttons()[1]);
  await tick();
  const message = sent.find((m) => m.method === "ui/message");
  assert.equal(message.params.role, "user");
  assert.match(message.params.content[0].text, /Explain the current result in “Sine wave” \(surface sine-wave, revision 3\)[\s\S]*Frequency 6\./);
  // The view keeps its original token: later results never carry one.
  view.bump(7);
  await tick(450);
  assert.equal(calls("save_ui_state").at(-1).params.arguments.token, "secret-token");
});

test("a failed computation keeps the previous result and explains why", async () => {
  const { reply, buttons, notice, userClick, out, calls } = await start(ALL);
  userClick(buttons()[0]);
  await tick();
  const [run] = calls("run_ui_action");
  await reply(run.id, { isError: true, content: [{ type: "text", text: "Numerical calculation failed" }], structuredContent: { code: "UI_ACTION_FAILED", revision: 1 } });
  assert.equal(out(), '{"frequency":2} dark');
  assert.equal(notice(), "Numerical calculation failed");
});

test("errors, native surfaces and foreign messages fall back to text", async () => {
  const native = await mount();
  await native.deliver(result(undefined));
  assert.equal(native.view.document.body.textContent, "A readable fallback.");

  const failed = await mount();
  await failed.deliver(result(SURFACE, { isError: true }));
  assert.equal(failed.out(), undefined);

  const foreign = await mount();
  await foreign.deliver(result(SURFACE), new JSDOM("").window);
  assert.equal(foreign.out(), undefined);
});

test("viewer answers teardown and rejects unknown host requests", async () => {
  const { sent, deliver } = await mount();
  await deliver({ jsonrpc: "2.0", id: 7, method: "ui/resource-teardown", params: { reason: "closed" } });
  await deliver({ jsonrpc: "2.0", id: 8, method: "ui/unknown", params: {} });
  assert.deepEqual(sent.find((m) => m.id === 7), { jsonrpc: "2.0", id: 7, result: {} });
  assert.equal(sent.find((m) => m.id === 8).error.code, -32601);
});
