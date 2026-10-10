import "../../test/jsdom-env.mjs";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { observePlotResize } from "./plotResize.ts";

const originalResizeObserver = globalThis.ResizeObserver;
let lastObserver: { fire: (width: number, height: number) => void; disconnected: boolean };
class ResizeObserverStub {
  disconnected = false;
  constructor(private callback: ResizeObserverCallback) { lastObserver = this; }
  observe() {}
  disconnect() { this.disconnected = true; }
  fire(width: number, height: number) { this.callback([{ contentRect: { width, height } } as ResizeObserverEntry], this as unknown as ResizeObserver); }
}
globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
afterEach(() => { document.body.replaceChildren(); });
function graph() {
  const container = document.createElement("div"), plot = document.createElement("div");
  container.appendChild(plot); document.body.appendChild(container);
  Object.defineProperties(plot, { clientWidth: { value: 400 }, clientHeight: { value: 380 } });
  return { container, plot };
}
const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 110));

test("container-only resize uses the supported graph API once after splitter movement settles", async () => {
  const { container, plot } = graph(); const resized: HTMLElement[] = [];
  const cleanup = observePlotResize(container, plot, (element) => resized.push(element));
  lastObserver.fire(400, 380); lastObserver.fire(420, 380); lastObserver.fire(440, 380);
  await settle(); assert.deepEqual(resized, [plot]);
  lastObserver.fire(440, 380); await settle(); assert.equal(resized.length, 1);
  cleanup(); assert.equal(lastObserver.disconnected, true);
});

test("purging or removing a plot cancels resize work and late observer callbacks cannot revive it", async () => {
  const { container, plot } = graph(); let calls = 0;
  const cleanup = observePlotResize(container, plot, () => calls++);
  lastObserver.fire(400, 380); cleanup(); lastObserver.fire(420, 380);
  await settle(); assert.equal(calls, 0);
  const another = observePlotResize(container, plot, () => calls++);
  lastObserver.fire(440, 380); plot.remove(); await settle(); assert.equal(calls, 0); another();
});

test("hidden zero-size containers do not request chart resize", async () => {
  const { container, plot } = graph(); let calls = 0;
  const cleanup = observePlotResize(container, plot, () => calls++);
  lastObserver.fire(0, 0); await settle(); assert.equal(calls, 0); cleanup();
});
