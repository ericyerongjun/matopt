/**
 * A jsdom document for the React unit lane, installed on `globalThis`.
 *
 * Imported for its side effect as the **first** import of a `.tsx` test, before
 * `react-dom` is evaluated (ESM evaluates imports depth-first in source order,
 * so position matters). Node 21+ defines `navigator` as a getter-only global,
 * hence `Object.defineProperty` rather than plain assignment.
 */

import { JSDOM } from 'jsdom'
import { after } from 'node:test'

const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  pretendToBeVisual: true,
  url: 'http://localhost/',
})

const { window } = dom

/** Globals React DOM reads; `undefined` ones (jsdom has no PointerEvent) are skipped. */
const WINDOW_KEYS = [
  'window',
  'document',
  'navigator',
  'location',
  'history',
  'HTMLElement',
  'customElements',
  'ShadowRoot',
  'HTMLTextAreaElement',
  'HTMLIFrameElement',
  'HTMLInputElement',
  'HTMLButtonElement',
  'Element',
  'Node',
  'DocumentFragment',
  'Text',
  'Event',
  'CustomEvent',
  'KeyboardEvent',
  'MouseEvent',
  'FocusEvent',
  'InputEvent',
  'MutationObserver',
  'ResizeObserver',
  'DOMParser',
  'SVGElement',
  'XMLSerializer',
]

for (const key of WINDOW_KEYS) {
  const value = window[key]
  if (value === undefined || value === null) continue
  try {
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true })
  } catch {
    // A non-configurable global that jsdom cannot supply is not worth failing over.
  }
}

for (const key of ['getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
  if (typeof window[key] !== 'function') continue
  Object.defineProperty(globalThis, key, {
    value: window[key].bind(window),
    configurable: true,
    writable: true,
  })
}

// React 19 reads this to decide whether `act()` should warn about unwrapped updates.
globalThis.IS_REACT_ACT_ENVIRONMENT = true

// Release jsdom's animation timers/listeners after each test file finishes.
after(() => window.close())

export { dom, window }
