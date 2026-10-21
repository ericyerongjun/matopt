import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { JSDOM } from "jsdom";

// The MCP Apps view served by the backend at ui://matopt/mini-app-viewer.html.
const VIEWER = readFileSync(new URL("../../backend/app/ui/mcp_app_viewer.html", import.meta.url), "utf8");
const APP = `<!doctype html><html><head><style>p{margin:0}</style></head><body><p id="out"></p>
<script>const draw=(s)=>{document.getElementById("out").textContent=JSON.stringify(s)+" "+matopt.getTheme()};
matopt.onStateChange(draw);draw(matopt.getState());window.bump=()=>matopt.setState({frequency:5,extra:1});</script></body></html>`;

async function mount() {
  const host = new JSDOM("").window;
  const sent = [];
  host.postMessage = (message) => sent.push(JSON.parse(JSON.stringify(message)));  // structured clone across realms
  let view;
  new JSDOM(VIEWER, { runScripts: "dangerously", pretendToBeVisual: true, beforeParse(window) {
    view = window;
    Object.defineProperty(window, "parent", { value: host, configurable: true });
    window.ResizeObserver = class { observe() {} };
  } });
  const tick = () => new Promise((resolve) => setTimeout(resolve, 30));
  const deliver = async (data, source = host) => { view.dispatchEvent(new view.MessageEvent("message", { data, source })); await tick(); };
  await tick();
  return { view, sent, deliver };
}

const result = (surface, extra = {}) => ({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: {
  content: [{ type: "text", text: "A readable fallback." }], structuredContent: { surface_id: "sine-wave" },
  _meta: surface ? { "matopt/surface": surface } : undefined, ...extra } });

test("viewer completes the MCP Apps handshake and adopts the host theme", async () => {
  const { view, sent, deliver } = await mount();
  assert.deepEqual(sent[0], { jsonrpc: "2.0", id: 1, method: "ui/initialize", params: {
    protocolVersion: "2026-01-26", appInfo: { name: "matopt-mini-app-viewer", version: "1" }, appCapabilities: {} } });
  await deliver({ jsonrpc: "2.0", id: 1, result: { protocolVersion: "2026-01-26", hostContext: { theme: "dark" } } });
  assert.equal(sent[1].method, "ui/notifications/initialized");
  assert.equal(view.document.documentElement.dataset.theme, "dark");
  await deliver({ jsonrpc: "2.0", method: "ui/notifications/host-context-changed", params: { theme: "light" } });
  assert.equal(view.document.documentElement.dataset.theme, "light");
});

test("tool result renders the saved mini-app with only its declared state", async () => {
  const { view, sent, deliver } = await mount();
  await deliver({ jsonrpc: "2.0", id: 1, result: { hostContext: { theme: "dark" } } });
  await deliver(result({ id: "sine-wave", revision: 1, html: APP, state: { frequency: 2 } }));
  const out = () => view.document.getElementById("out").textContent;
  assert.equal(out(), '{"frequency":2} dark');
  view.bump();
  assert.equal(out(), '{"frequency":5} dark');
  assert.ok(sent.some((m) => m.method === "ui/notifications/size-changed" && m.params.height >= 160));
  // Later results never replace the mounted app.
  await deliver(result({ id: "other", revision: 1, html: "<p id=out>replaced</p>", state: {} }));
  assert.equal(out(), '{"frequency":5} dark');
});

test("errors, native surfaces and foreign messages fall back to text", async () => {
  const native = await mount();
  await native.deliver(result(undefined));
  assert.equal(native.view.document.body.textContent, "A readable fallback.");

  const failed = await mount();
  await failed.deliver(result({ id: "x", revision: 1, html: APP, state: {} }, { isError: true }));
  assert.equal(failed.view.document.getElementById("out"), null);

  const foreign = await mount();
  await foreign.deliver(result({ id: "x", revision: 1, html: APP, state: {} }), new JSDOM("").window);
  assert.equal(foreign.view.document.getElementById("out"), null);
});

test("viewer answers teardown and rejects unknown host requests", async () => {
  const { sent, deliver } = await mount();
  await deliver({ jsonrpc: "2.0", id: 7, method: "ui/resource-teardown", params: { reason: "closed" } });
  await deliver({ jsonrpc: "2.0", id: 8, method: "ui/unknown", params: {} });
  assert.deepEqual(sent.find((m) => m.id === 7), { jsonrpc: "2.0", id: 7, result: {} });
  assert.equal(sent.find((m) => m.id === 8).error.code, -32601);
});
