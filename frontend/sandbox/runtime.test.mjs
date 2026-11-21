import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { JSDOM, VirtualConsole } from "jsdom";

const runtime = readFileSync(new URL("./runtime.js", import.meta.url), "utf8");

async function render(html, { brokenLibrary = false, state = {}, standalone = false } = {}) {
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
  // A host sends init after the isolated page is ready.
  if (window.document.readyState !== "complete") await new Promise((resolve) => window.addEventListener("load", resolve, { once: true }));
  const init = new window.MessageEvent("message", { data: {
    type: "matopt:init", version: 1, channel: "test", surfaceId: "graph", html, state, standalone, theme: "light",
  } });
  Object.defineProperty(init, "source", { value: parent });
  window.dispatchEvent(init);
  await new Promise((resolve) => setTimeout(resolve, 50));
  return { dom, requests, messages };
}

for (const standalone of [false, true]) {
  test(`parameter edits recompute a numeric output in a ${standalone ? "standalone preview" : "saved mini-app"}`, async () => {
    const html = `<input id="scale" type="range" min="1" max="8"><output id="area"></output><script>
      document.addEventListener("DOMContentLoaded", () => {
        function draw({scale = 2}) {
          document.querySelector("input").value = scale;
          document.querySelector("output").value = scale * scale;
        }
        matopt.onStateChange(draw);
        document.querySelector("input").oninput = e => matopt.setState({scale: +e.target.value});
        draw(matopt.getState());
      });
    </script>`;
    const { dom, messages } = await render(html, { standalone, state: standalone ? {} : { scale: 2 } });
    try {
      const input = dom.window.document.querySelector("input");
      const output = dom.window.document.querySelector("output");
      assert.equal(output.value, "4");
      input.value = "5";
      input.dispatchEvent(new dom.window.Event("input"));
      assert.equal(output.value, "25");
      assert.equal(messages.filter((message) => message.type === "matopt:state").length, standalone ? 0 : 1);
    } finally { dom.window.close(); }
  });
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

test("Escape inside an interactive frame can return to its chat", async () => {
  const { dom, messages } = await render('<input id="frequency">');
  try {
    dom.window.document.querySelector("input").dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    assert.ok(messages.some((message) => message.type === "matopt:escape" && message.channel === "test" && message.surfaceId === "graph"));
  } finally { dom.window.close(); }
});
