import "../../test/jsdom-env.mjs";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { chartJsDefaults, mergeChartDefaults, plotlyDefaults, readChartTheme, themedChartData, useChartTheme } from "./chartTheme.ts";

let root: Root | null = null, container: HTMLDivElement | null = null;
const nativeMutationObserver = globalThis.MutationObserver;
afterEach(() => { act(() => root?.unmount()); root = null; container?.remove(); container = null; globalThis.MutationObserver = nativeMutationObserver; document.documentElement.removeAttribute("style"); document.documentElement.classList.remove("test-dark"); document.querySelector('#chart-test-style')?.remove(); });
function setColors() {
  const style = document.createElement("style"); style.id = "chart-test-style";
  style.textContent = ":root { --color-foreground: #111111; --color-foreground-subtle: #333333; --color-background: #ffffff; --color-border: rgba(0,0,0,.1); --color-usage-chart-1: #0066ff; --ui-font-size: 14px; } :root.test-dark { --color-foreground: #eeeeee; --color-background: #222222; --color-usage-chart-1: #66aaff; }";
  document.head.appendChild(style);
}

test("chart theme follows native root class and font scalar changes on an already mounted figure", async () => {
  setColors();
  // Keep real asynchronous mutation delivery while enclosing its React update in synchronous act.
  globalThis.MutationObserver = class extends nativeMutationObserver {
    constructor(callback: MutationCallback) { super((records, observer) => act(() => callback(records, observer))); }
  };
  function ThemePreview() { const theme = useChartTheme(); return <output>{JSON.stringify(theme)}</output>; }
  container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
  act(() => root?.render(<ThemePreview />));
  assert.match(container.textContent!, /#111111/);
  act(() => { document.documentElement.classList.add("test-dark"); document.documentElement.style.setProperty("--ui-font-size", "18px"); });
  await Promise.resolve(); await new Promise<void>((resolve) => setImmediate(resolve)); act(() => flushSync(() => {}));
  const value = JSON.parse(container.textContent!);
  assert.equal(value.foreground, "#eeeeee"); assert.equal(value.background, "#222222"); assert.equal(value.fontSize, 18); assert.deepEqual(value.colorway, ["#66aaff"]);
});

test("Plotly figure axes, colors, interaction state and config overrides survive native defaults", () => {
  setColors(); const theme = readChartTheme();
  const supplied = { font: { size: 20 }, xaxis: { title: { text: "iterations" }, range: [0, 8], gridcolor: "red" }, colorway: ["purple"], showlegend: false, uirevision: "keep-zoom", legend: null };
  const layout = mergeChartDefaults(plotlyDefaults(theme), supplied);
  assert.equal(layout.font?.size, 20); assert.equal(layout.font?.color, "#111111");
  assert.equal(layout.xaxis?.gridcolor, "red"); assert.equal(layout.yaxis?.gridcolor, "rgba(0,0,0,.1)");
  assert.deepEqual(layout.xaxis?.range, [0, 8]); assert.deepEqual(layout.colorway, ["purple"]);
  assert.equal(layout.showlegend, false); assert.equal(layout.uirevision, "keep-zoom"); assert.equal(layout.legend, null);
  assert.equal(supplied.font.size, 20); assert.deepEqual(supplied.xaxis.range, [0, 8]);
});

test("Chart.js honors explicit dataset styles and named axes while filling absent theme defaults", () => {
  setColors(); const theme = readChartTheme();
  const options = { plugins: { legend: { display: false } }, scales: { custom: { type: "logarithmic", grid: { display: false } } } };
  const resolved = mergeChartDefaults(chartJsDefaults(theme, "line", options), options);
  assert.equal((resolved.plugins.legend as Record<string, unknown>).display, false);
  assert.equal(resolved.plugins.legend.labels.color, "#111111");
  assert.deepEqual(Object.keys(resolved.scales), ["custom"]);
  assert.equal((resolved.scales.custom.grid as Record<string, unknown>).display, false);
  const data = { datasets: [{ data: [1, 2] }, { data: [3, 4], borderColor: "orange", backgroundColor: null }] };
  const themed = themedChartData(data, "line", theme);
  assert.deepEqual(themed.datasets[0], { data: [1, 2], borderColor: "#0066ff", backgroundColor: "#0066ff" });
  assert.deepEqual(themed.datasets[1], { data: [3, 4], borderColor: "orange", backgroundColor: null });
  assert.deepEqual(data.datasets[0], { data: [1, 2] });
  assert.deepEqual(chartJsDefaults(theme, "pie").scales, {});
});
