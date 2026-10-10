/* Maintained render-subtree migration from ZCode 29628c9acdb81b703bbd4080c207a0e7ce5e276e:
 * packages/ui/src/app-shell/WorkspaceShellLayout.tsx (native frame/sidebar/separator/conversation panels)
 * and app-shell/AnimatedSidePanePanel.tsx (native right-pane separator).
 * Apache-2.0. MatOpt host seam replaces Electron/RPC orchestration with React slots.
 * Native render JSX/classes and pure geometry helpers are preserved; unavailable terminal is omitted. */
import { useEffect, useRef, useState, type CSSProperties, type PointerEvent, type ReactNode } from "react";
import { cn } from "@/components/lib/utils.js";
import { DesktopWindowFrame } from "@/DesktopWindowFrame.js";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable.js";
import { SIDE_PANE_DEFAULT_EXPANDED_SIZE } from "@/app-shell/sidePaneLayout.js";
import { resolveWorkspaceShellPanelRadiusPx, resolveWorkspaceShellResizeHandleInsetPx } from "@/app-shell/workspaceShellWindowChrome.js";

const WORKSPACE_SIDEBAR_MIN_WIDTH_PX = 264;
const WORKSPACE_SIDEBAR_WIDTH_KEY = "matopt.workspace-sidebar-width";
function readSidebarWidth() {
  try { const value = Number(localStorage.getItem(WORKSPACE_SIDEBAR_WIDTH_KEY)); return Number.isFinite(value) && value >= WORKSPACE_SIDEBAR_MIN_WIDTH_PX ? value : WORKSPACE_SIDEBAR_MIN_WIDTH_PX; }
  catch { return WORKSPACE_SIDEBAR_MIN_WIDTH_PX; }
}

export function WorkspaceShellLayout({ sidebar, header, conversation, sidePane, topOverlay, isSidebarVisible, isSidePaneVisible, onDismissSidebar }: {
  sidebar: ReactNode; header: ReactNode; conversation: ReactNode; sidePane: ReactNode; topOverlay: ReactNode;
  isSidebarVisible: boolean; isSidePaneVisible: boolean;
  onDismissSidebar?: () => void;
}) {
  const [sidebarWidth, setSidebarWidth] = useState(readSidebarWidth);
  const [mobile, setMobile] = useState(() => window.matchMedia("(max-width: 800px)").matches);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 800px)");
    const onChange = () => setMobile(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  const shellRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!mobile || !isSidebarVisible) return;
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      // Tooltips are informational: one Escape still closes the drawer. Native
      // menus/dialogs take the first Escape themselves, including portaled layers.
      const interactiveLayer = Array.from(document.querySelectorAll<HTMLElement>(
        '[role="menu"][data-state="open"], [role="listbox"]:not([data-state="closed"]), [role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"]',
      )).some((layer) => !layer.closest('[hidden], [inert], [aria-hidden="true"], [data-state="closed"]'));
      if (interactiveLayer) return;
      event.preventDefault();
      onDismissSidebar?.();
    };
    window.addEventListener("keydown", handleEscape, true);
    shellRef.current?.querySelector<HTMLElement>('#sidebar [role="button"], #sidebar button, #sidebar [tabindex="0"]')?.focus();
    return () => window.removeEventListener("keydown", handleEscape, true);
  }, [mobile, isSidebarVisible, onDismissSidebar]);
  const dragRef = useRef<{ startX: number; startWidth: number; width: number; pointer: number } | null>(null);
  const clampWidth = (width: number) => Math.max(Math.min(WORKSPACE_SIDEBAR_MIN_WIDTH_PX, (shellRef.current?.clientWidth ?? 1200) / 2), Math.min(width, (shellRef.current?.clientWidth ?? 1200) / 2));
  const applyWidth = (width: number) => shellRef.current?.style.setProperty("--workspace-sidebar-panel-width", `${width}px`);
  const saveWidth = (width: number) => { setSidebarWidth(width); try { localStorage.setItem(WORKSPACE_SIDEBAR_WIDTH_KEY, String(width)); } catch { /* optional preference */ } };
  const finishResize = (event: PointerEvent<HTMLDivElement>, cancel = false) => {
    const drag = dragRef.current; if (!drag || drag.pointer !== event.pointerId) return;
    dragRef.current = null; delete shellRef.current?.dataset.workspaceSidebarResizing;
    const width = cancel ? drag.startWidth : drag.width;
    applyWidth(width); saveWidth(width);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const workspaceShellSplitStyle = {
    "--workspace-sidebar-panel-width": isSidebarVisible ? `${sidebarWidth}px` : "0px",
    "--workspace-panel-radius": `${resolveWorkspaceShellPanelRadiusPx({})}px`,
    "--workspace-resize-handle-inset": `${resolveWorkspaceShellResizeHandleInsetPx({})}px`,
  } as CSSProperties;
  return <DesktopWindowFrame title="MatOpt">
    <div ref={shellRef} data-workspace-shell="true" style={workspaceShellSplitStyle} className="relative flex h-full min-h-0 w-full overflow-hidden">
      {mobile && isSidebarVisible && <button type="button" className="absolute inset-0 z-20 bg-foreground/10" aria-label="Close sidebar" onClick={onDismissSidebar} />}
      <div data-panel="" data-workspace-sidebar-panel="true" id="sidebar" role={mobile && isSidebarVisible ? "dialog" : undefined} aria-modal={mobile && isSidebarVisible ? true : undefined} aria-label={mobile && isSidebarVisible ? "Workspace sidebar" : undefined} className={cn("w-[var(--workspace-sidebar-panel-width)] flex-none overflow-hidden duration-200 ease-out transition-[width,opacity] data-[workspace-sidebar-resizing=true]:transition-opacity", mobile ? "absolute inset-y-0 left-0 z-30 max-w-[85%] border-r border-border bg-background-win-alt shadow-xl" : "max-w-[50%]", isSidebarVisible ? "opacity-100" : "pointer-events-none opacity-0")}>
        <aside className="h-full overflow-hidden select-none" aria-hidden={!isSidebarVisible}>{isSidebarVisible && sidebar}</aside>
        {mobile && isSidebarVisible && topOverlay}
      </div>
      {isSidebarVisible && !mobile ? <div role="separator" tabIndex={0} aria-controls="sidebar" aria-label="Resize sidebar" aria-orientation="vertical"
        aria-valuemin={WORKSPACE_SIDEBAR_MIN_WIDTH_PX} aria-valuenow={Math.round(sidebarWidth)} data-testid="resizable-handle"
        onKeyDown={(event) => { if (!["ArrowLeft", "ArrowRight", "Home"].includes(event.key)) return; event.preventDefault(); saveWidth(clampWidth(event.key === "Home" ? WORKSPACE_SIDEBAR_MIN_WIDTH_PX : sidebarWidth + (event.key === "ArrowRight" ? 16 : -16))); }}
        onPointerDown={(event) => { if (event.button !== 0) return; event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); dragRef.current = { startX: event.clientX, startWidth: sidebarWidth, width: sidebarWidth, pointer: event.pointerId }; if (shellRef.current) shellRef.current.dataset.workspaceSidebarResizing = "true"; }}
        onPointerMove={(event) => { const drag = dragRef.current; if (!drag || drag.pointer !== event.pointerId) return; drag.width = clampWidth(drag.startWidth + event.clientX - drag.startX); applyWidth(drag.width); }}
        onPointerCancel={(event) => finishResize(event, true)} onPointerUp={(event) => finishResize(event)}
        className={cn("group/handle relative z-10 flex h-full w-1 shrink-0 touch-none cursor-ew-resize items-center justify-center bg-transparent outline-none [app-region:no-drag] focus:outline-none focus-visible:ring-0", "after:pointer-events-none after:absolute after:rounded-full after:bg-foreground-subtlest/50 after:opacity-0 after:transition-opacity after:content-[''] after:inset-y-[var(--workspace-panel-radius)] after:w-0.5", "hover:after:opacity-100 data-[separator=hover]:after:opacity-100 data-[separator=active]:after:opacity-100 focus-visible:after:opacity-100 [[data-workspace-sidebar-resizing=true]_&]:after:opacity-100", "after:inset-y-[var(--workspace-resize-handle-inset)]")} /> : null}
      <div data-panel="" id="content" inert={mobile && isSidebarVisible} className={cn("flex min-w-[320px] flex-1 flex-col p-1 pt-0", mobile ? "pl-1" : "pl-0")}>
        <div className="h-1 w-full [app-region:drag]" />
        {mobile ? <div className="min-h-0 flex-1">
          {isSidePaneVisible ? sidePane : <section data-workspace-conversation-frame="true" className="relative flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-background rounded-[var(--workspace-panel-radius)] border border-border">{header}<div className="min-h-0 flex-1 overflow-hidden"><main className="relative flex h-full min-h-0 flex-1 flex-col overflow-hidden">{conversation}</main></div></section>}
        </div> : <ResizablePanelGroup orientation="horizontal" layoutId="matopt-workspace-body-layout" panelIds={isSidePaneVisible ? ["conversation-column", "browser"] : ["conversation-column"]} className="min-h-0 flex-1">
          <ResizablePanel id="conversation-column" minSize="35%" defaultSize={isSidePaneVisible ? "55%" : undefined}>
            <section data-workspace-conversation-frame="true" className="relative flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-background rounded-[var(--workspace-panel-radius)] border border-border">
              {header}
              <div className="min-h-0 flex-1 overflow-hidden"><main className="relative flex h-full min-h-0 flex-1 flex-col overflow-hidden">{conversation}</main></div>
            </section>
          </ResizablePanel>
          {isSidePaneVisible && <><ResizableHandle data-workspace-side-pane-resize-handle="true" className={cn(
            "aria-[orientation=vertical]:w-1 aria-[orientation=vertical]:translate-x-0 aria-[orientation=vertical]:my-0 aria-[orientation=vertical]:h-full",
            "hover:bg-transparent data-[separator=hover]:bg-transparent data-[separator=active]:bg-transparent focus-visible:bg-transparent",
            "aria-[orientation=vertical]:[mask-image:none] aria-[orientation=vertical]:[-webkit-mask-image:none]",
            "after:pointer-events-none after:absolute after:rounded-full after:bg-foreground-subtlest/50 after:opacity-0 after:transition-opacity after:content-[''] after:inset-y-[var(--workspace-panel-radius,var(--radius-xl))] after:w-0.5",
            "hover:after:opacity-100 data-[separator=hover]:after:opacity-100 data-[separator=active]:after:opacity-100 focus-visible:after:opacity-100",
          )} /><ResizablePanel id="browser" minSize="25%" defaultSize={SIDE_PANE_DEFAULT_EXPANDED_SIZE}>{sidePane}</ResizablePanel></>}
        </ResizablePanelGroup>}
      </div>
      {(!mobile || (!isSidePaneVisible && !isSidebarVisible)) && topOverlay}
    </div>
  </DesktopWindowFrame>;
}
