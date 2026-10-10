/** Browser APIs needed by the actual native @pierre/diffs shadow-DOM viewer.
 * jsdom supplies the DOM and custom elements; constructable stylesheets and
 * layout observation are unavailable. Source assertions do not depend on layout. */
import './jsdom-env.mjs';

for (const name of ['HTMLPreElement', 'HTMLStyleElement']) {
  Object.defineProperty(globalThis, name, { configurable: true, value: window[name] });
}
if (typeof globalThis.CSSStyleSheet === 'undefined') {
  Object.defineProperty(globalThis, 'CSSStyleSheet', {
    configurable: true,
    value: class { replaceSync(_source) {} },
  });
}
if (typeof globalThis.ResizeObserver === 'undefined') {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class { observe() {} disconnect() {} unobserve() {} },
  });
}
