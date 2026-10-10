import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

const WIDTH_KEY = "matopt.workspaceWidth";
function initialWidth() {
  try { const width = Number(window.localStorage.getItem(WIDTH_KEY)); if (width >= 32 && width <= 68) return width; } catch { /* Optional preference. */ }
  return 50;
}

/** Each workspace stays mounted; only its visibility changes when opening an inspector. */
export function MatOptShell({ sidebar, header, conversation, sidePane, inspector, sidebarOpen, paneOpen, inspectorOpen = false, onCloseSidebar, onClosePane, onCloseInspector }: {
  sidebar: ReactNode; header: ReactNode; conversation: ReactNode; sidePane: ReactNode; inspector?: ReactNode;
  sidebarOpen: boolean; paneOpen: boolean; inspectorOpen?: boolean;
  onCloseSidebar: () => void; onClosePane: () => void; onCloseInspector?: () => void;
}) {
  const [small, setSmall] = useState(() => window.matchMedia("(max-width: 900px)").matches);
  const [compact, setCompact] = useState(small);
  const [paneWidth, setPaneWidth] = useState(initialWidth);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const paneRef = useRef<HTMLElement>(null);
  const inspectorRef = useRef<HTMLElement>(null);
  const sidebarFocus = useRef<HTMLElement | null>(null);
  const paneFocus = useRef<HTMLElement | null>(null);
  const inspectorFocus = useRef<HTMLElement | null>(null);
  const drag = useRef<{ left: number; width: number } | null>(null);
  const drawerOpen = small && sidebarOpen;
  useEffect(() => {
    const media = window.matchMedia("(max-width: 900px)");
    const update = () => {
      setSmall(media.matches);
      const width = workspaceRef.current?.getBoundingClientRect().width;
      setCompact(media.matches || Boolean(width && width < 760));
    };
    update(); media.addEventListener("change", update);
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(update) : null;
    if (workspaceRef.current) observer?.observe(workspaceRef.current);
    return () => { media.removeEventListener("change", update); observer?.disconnect(); };
  }, []);
  useEffect(() => { try { window.localStorage.setItem(WIDTH_KEY, String(paneWidth)); } catch { /* Optional preference. */ } }, [paneWidth]);
  useEffect(() => {
    if (!drawerOpen) return;
    sidebarFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    sidebarRef.current?.querySelector<HTMLElement>("button, input, [tabindex='0']")?.focus({ preventScroll: true });
    return () => {
      // The compact header can remount its opener while the drawer closes.
      const opener = workspaceRef.current?.closest(".matopt-shell")?.querySelector<HTMLElement>('[aria-label="Open sidebar"]');
      const previous = sidebarFocus.current;
      (previous?.isConnected && previous !== document.body ? previous : opener)?.focus({ preventScroll: true });
    };
  }, [drawerOpen]);
  useEffect(() => {
    if (!paneOpen) return;
    paneFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (compact) paneRef.current?.querySelector<HTMLElement>("[data-pane-heading]")?.focus({ preventScroll: true });
    return () => { if (paneFocus.current?.isConnected) paneFocus.current.focus({ preventScroll: true }); };
  }, [paneOpen, compact]);
  useEffect(() => {
    if (!inspectorOpen) return;
    inspectorFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    inspectorRef.current?.querySelector<HTMLElement>("[data-pane-heading]")?.focus({ preventScroll: true });
    return () => { if (inspectorFocus.current?.isConnected) inspectorFocus.current.focus({ preventScroll: true }); };
  }, [inspectorOpen]);
  const resize = (width: number) => setPaneWidth(Math.max(32, Math.min(68, Math.round(width))));

  return <div className="matopt-shell" data-sidebar-open={sidebarOpen} data-pane-open={paneOpen} data-compact={compact} data-inspector-open={inspectorOpen}>
    {drawerOpen && <button type="button" className="matopt-sidebar-backdrop" aria-label="Close sidebar" tabIndex={-1} onClick={onCloseSidebar} />}
    <aside ref={sidebarRef} className="matopt-sidebar" hidden={!sidebarOpen}
      role={drawerOpen ? "dialog" : undefined} aria-modal={drawerOpen || undefined} aria-label="Chat navigation"
      onKeyDown={(event) => {
        if (!drawerOpen || event.defaultPrevented) return;
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onCloseSidebar(); }
        if (event.key !== "Tab") return;
        const elements = Array.from(sidebarRef.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), [tabindex='0']") ?? []).filter((element) => !element.closest("[hidden]"));
        const first = elements[0], last = elements.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus({ preventScroll: true }); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus({ preventScroll: true }); }
      }}>{sidebar}</aside>
    <main className="matopt-main" inert={drawerOpen || undefined}>
      <header className="matopt-header">{header}</header>
      <div ref={workspaceRef} className="matopt-workspace" style={{ "--workspace-width": `${paneWidth}%` } as CSSProperties}>
        <section className="matopt-conversation" aria-label="Conversation" inert={compact && (paneOpen || inspectorOpen) || undefined}>{conversation}</section>
        <div className="matopt-workspace-splitter" role="separator" aria-label="Resize result workspace" aria-orientation="vertical" aria-valuemin={32} aria-valuemax={68} aria-valuenow={paneWidth} aria-valuetext={`Workspace ${paneWidth} percent`}
          hidden={!paneOpen || compact || inspectorOpen} tabIndex={paneOpen && !compact && !inspectorOpen ? 0 : -1}
          onKeyDown={(event) => {
            const next = event.key === "ArrowLeft" ? paneWidth + 2 : event.key === "ArrowRight" ? paneWidth - 2 : event.key === "Home" ? 32 : event.key === "End" ? 68 : null;
            if (next !== null) { event.preventDefault(); resize(next); }
          }}
          onDoubleClick={() => resize(50)}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            const rect = workspaceRef.current?.getBoundingClientRect(); if (!rect?.width) return;
            drag.current = { left: rect.left, width: rect.width }; event.currentTarget.setPointerCapture(event.pointerId); event.preventDefault();
          }}
          onPointerMove={(event) => { if (drag.current) resize(100 - (event.clientX - drag.current.left) / drag.current.width * 100); }}
          onPointerUp={(event) => { drag.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
          onLostPointerCapture={() => { drag.current = null; }} />
        <section ref={paneRef} className="matopt-side-pane" aria-label="Workspace panel" hidden={!paneOpen || inspectorOpen} inert={inspectorOpen || undefined}
          onKeyDown={(event) => {
            if (event.key === "Escape" && !event.defaultPrevented && !(event.target as HTMLElement).closest("[role=dialog], [role=menu]")) { event.preventDefault(); event.stopPropagation(); onClosePane(); }
          }}>{sidePane}</section>
        <aside ref={inspectorRef} className="matopt-inspector" aria-label="Activity inspector" hidden={!inspectorOpen}
          onKeyDown={(event) => { if (event.key === "Escape" && !event.defaultPrevented) { event.preventDefault(); event.stopPropagation(); onCloseInspector?.(); } }}>{inspector}</aside>
      </div>
    </main>
  </div>;
}
