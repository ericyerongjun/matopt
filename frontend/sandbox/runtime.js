/* Runs on a dedicated static origin inside an opaque-origin sandbox. No API access. */
(() => {
  "use strict";
  const params = new URLSearchParams(location.hash.slice(1));
  const channel = params.get("channel");
  const surfaceId = params.get("surface");
  if (!channel || !surfaceId || parent === window) return;
  let initialized = false;
  let state = {};
  let theme = "light";
  let lastHeight = 0;
  const stateListeners = new Set();
  const themeListeners = new Set();
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const post = (type, payload = {}) => parent.postMessage({ type, version: 1, channel, surfaceId, ...payload }, "*");
  function validState(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    try { return JSON.stringify(value).length <= 65536; } catch { return false; }
  }
  function resize() {
    const height = Math.max(160, Math.min(1200, Math.ceil(document.body?.scrollHeight || 160)));
    if (height !== lastHeight) { lastHeight = height; post("matopt:resize", { height }); }
  }
  function setState(next, notify = true) {
    if (!validState(next)) return;
    // Only keys declared in the initial server-owned state may cross the bridge.
    const allowed = {};
    for (const key of Object.keys(state)) if (Object.hasOwn(next, key)) allowed[key] = next[key];
    state = { ...state, ...copy(allowed) };
    for (const listener of stateListeners) { try { listener(copy(state)); } catch { /* app-local error */ } }
    if (notify) post("matopt:state", { state });
    requestAnimationFrame(resize);
  }
  function setTheme(next) {
    theme = next === "dark" ? "dark" : "light";
    document.documentElement.dataset.theme = theme;
    for (const listener of themeListeners) { try { listener(theme); } catch { /* app-local error */ } }
  }
  const api = Object.freeze({
    getState: () => copy(state), setState,
    getTheme: () => theme, resize,
    onStateChange(listener) { if (typeof listener !== "function") return () => {}; stateListeners.add(listener); return () => stateListeners.delete(listener); },
    onThemeChange(listener) { if (typeof listener !== "function") return () => {}; themeListeners.add(listener); return () => themeListeners.delete(listener); },
  });
  Object.defineProperty(window, "matopt", { value: api, writable: false, configurable: false });

  function render(html) {
    if (typeof html !== "string" || html.length > 2_000_000) return;
    const parsed = new DOMParser().parseFromString(html, "text/html");
    // Navigation, forms, external scripts and assets are blocked again by HTTP CSP
    // and the embedding document's frame-src policy. Keep the runtime header intact.
    parsed.querySelectorAll("base, meta[http-equiv], link, script[src]").forEach((node) => node.remove());
    const executableType = (script) => !script.type || ["module", "text/javascript", "application/javascript"].includes(script.type.toLowerCase());
    for (const node of [...parsed.head.children]) {
      if (node.tagName === "STYLE" || (node.tagName === "SCRIPT" && !executableType(node))) document.head.appendChild(document.importNode(node, true));
    }
    const scripts = [...parsed.querySelectorAll("script")].filter(executableType).map((script) => ({ text: script.textContent || "", type: script.type }));
    parsed.querySelectorAll("script").forEach((script) => { if (executableType(script)) script.remove(); });
    document.body.replaceChildren(...[...parsed.body.childNodes].map((node) => document.importNode(node, true)));
    for (const script of scripts) {
      if (script.type && !["module", "text/javascript", "application/javascript"].includes(script.type)) continue;
      const executable = document.createElement("script");
      if (script.type) executable.type = script.type;
      executable.textContent = script.text;
      document.body.appendChild(executable);
    }
    new ResizeObserver(resize).observe(document.body);
    requestAnimationFrame(resize);
  }
  addEventListener("message", (event) => {
    const message = event.data;
    if (event.source !== parent || !message || message.version !== 1 || message.channel !== channel || message.surfaceId !== surfaceId) return;
    if (message.type === "matopt:init" && !initialized) {
      if (!validState(message.state) || typeof message.html !== "string") return;
      initialized = true; state = copy(message.state); setTheme(message.theme); render(message.html);
    } else if (initialized && message.type === "matopt:state") setState(message.state, false);
    else if (initialized && message.type === "matopt:theme") setTheme(message.theme);
  });
  document.addEventListener("click", (event) => {
    if (event.target instanceof Element && event.target.closest("a[href]")) event.preventDefault();
  }, true);
  document.addEventListener("submit", (event) => event.preventDefault(), true);
  post("matopt:ready");
})();
