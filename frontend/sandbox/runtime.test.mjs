import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { JSDOM, VirtualConsole } from "jsdom";

const runtime = readFileSync(new URL("./runtime.js", import.meta.url), "utf8");

async function render(html, { brokenLibrary = false } = {}) {
  const requests = [];
  const messages = [];
  const dom = new JSDOM('<!doctype html><body></body>', {
    url: "http://preview.invalid/#channel=test&surface=graph", runScripts: "dangerously",
    pretendToBeVisual: true, virtualConsole: new VirtualConsole(),
  });
  const { window } = dom;
  const parent = { postMessage: (message) => messages.push(message) };
  Object.defineProperty(window, "parent", { value: parent });
  window.ResizeObserver = class { observe() {} };
  const append = window.document.head.appendChild.bind(window.document.head);
  window.document.head.appendChild = (node) => {
    if (node instanceof window.HTMLScriptElement && node.src) {
      requests.push(node.src);
      queueMicrotask(() => {
        if (!brokenLibrary) window.eval('window.Plotly = { newPlot: () => { document.getElementById("graph").textContent = "Rendered locally"; } };');
        node.dispatchEvent(new window.Event(brokenLibrary ? "error" : "load"));
      });
    }
    return append(node);
  };
  window.eval(runtime);
  const init = new window.MessageEvent("message", { data: {
    type: "matopt:init", version: 1, channel: "test", surfaceId: "graph", html, state: {}, theme: "light",
  } });
  Object.defineProperty(init, "source", { value: parent });
  window.dispatchEvent(init);
  await new Promise((resolve) => setTimeout(resolve, 50));
  return { dom, requests, messages };
}

test("Plotly CDN exports render in order using only the locked local library", async () => {
  const { dom, requests, messages } = await render('<script>window.configured = true;</script><script src="https://cdn.plot.ly/plotly-4.1.1.min.js"></script><div id="graph"></div><script>if (!window.configured) throw Error("order"); Plotly.newPlot("graph", []);</script>');
  try {
    assert.deepEqual(requests, ["http://preview.invalid/plotly.min.js"]);
    assert.equal(dom.window.document.getElementById("graph").textContent, "Rendered locally");
    assert.equal(messages.some((message) => message.type === "matopt:error"), false);
  } finally { dom.window.close(); }
});

test("unapproved external scripts are never fetched and report a visible preview failure", async () => {
  const { dom, requests, messages } = await render('<script src="https://cdn.plot.ly.evil.invalid/plotly-4.1.1.min.js"></script>');
  try {
    assert.deepEqual(requests, []);
    assert.equal(messages.some((message) => message.type === "matopt:error" && message.channel === "test" && message.surfaceId === "graph"), true);
  } finally { dom.window.close(); }
});

test("missing local libraries and generated script errors report failure instead of a blank preview", async () => {
  /** @type {Array<[string, {brokenLibrary?: boolean}]>} */
  const cases = [
    ['<script src="https://cdn.plot.ly/plotly-4.1.1.min.js"></script>', { brokenLibrary: true }],
    ['<script>throw new Error("Generated app failed");</script>', {}],
  ];
  for (const [html, options] of cases) {
    const { dom, messages } = await render(html, options);
    try { assert.equal(messages.some((message) => message.type === "matopt:error"), true); }
    finally { dom.window.close(); }
  }
});
