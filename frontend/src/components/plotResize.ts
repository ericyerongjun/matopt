/** Container resizing for native ADE splits, coalesced and released with the actual graph. */
export function observePlotResize(container: HTMLElement, graph: HTMLElement, resize: (graph: HTMLElement) => unknown): () => void {
  if (typeof ResizeObserver === "undefined") return () => {};
  let active = true;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let lastWidth = -1, lastHeight = -1;
  const observer = new ResizeObserver((entries) => {
    if (!active) return;
    const size = entries[0]?.contentRect;
    if (!size || size.width <= 0 || size.height <= 0 || (size.width === lastWidth && size.height === lastHeight)) return;
    lastWidth = size.width; lastHeight = size.height;
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (!active || !graph.isConnected || graph.clientWidth <= 0 || graph.clientHeight <= 0) return;
      try { void Promise.resolve(resize(graph)).catch(() => {}); }
      catch { /* A graph may be hidden or purged during native pane navigation. */ }
    }, 80);
  });
  observer.observe(container);
  return () => { active = false; clearTimeout(timer); observer.disconnect(); };
}
