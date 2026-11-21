import { createContext, useContext, useId, useLayoutEffect, useRef, useState, type Dispatch, type ReactNode, type RefObject, type SetStateAction } from "react";
import { createPortal } from "react-dom";
import { Maximize2, Minimize2 } from "lucide-react";
import "./intelligent-ui.css";

export const PreviewPanelContext = createContext<{
    activeId: string | null;
    select: Dispatch<SetStateAction<string | null>>;
    panel: RefObject<HTMLElement | null>;
} | null>(null);

export function ExpandablePreview({ title, children }: { title: string; children: (toggle: ReactNode) => ReactNode }) {
    const workspace = useContext(PreviewPanelContext);
    const id = useId();
    const inline = useRef<HTMLDivElement>(null);
    const button = useRef<HTMLButtonElement>(null);
    const [host] = useState(() => workspace && typeof document !== "undefined" ? document.createElement("div") : null);
    const expanded = workspace?.activeId === id;
    const panel = workspace?.panel;
    const select = workspace?.select;
    const restoreFocus = useRef(false);

    useLayoutEffect(() => {
        if (!host || !inline.current) return;
        host.className = "ui-preview-block";
        const target = expanded ? panel?.current : inline.current;
        if (!target || host.parentNode === target) return;
        // An atomic move keeps the live iframe document, JS heap and playback position.
        if (host.isConnected && typeof target.moveBefore === "function") target.moveBefore(host, null);
        else target.appendChild(host);
        if (!expanded) inline.current.style.minHeight = "";
        if (expanded || restoreFocus.current) button.current?.focus({ preventScroll: true });
        restoreFocus.current = false;
    }, [expanded, host, panel]);

    useLayoutEffect(() => () => {
        host?.remove();
        select?.((current) => current === id ? null : current);
    }, [host, id, select]);

    if (!workspace || !host) return children(null);
    const collapse = () => { restoreFocus.current = true; workspace.select(null); };
    const label = expanded ? "Close expanded preview" : "Expand preview";
    const Icon = expanded ? Minimize2 : Maximize2;
    const toggle = <button ref={button} type="button" className="ui-text-button" title={label} aria-label={label}
        aria-controls={id} aria-expanded={expanded} onClick={() => {
            if (expanded) { collapse(); return; }
            if (inline.current) inline.current.style.minHeight = `${inline.current.getBoundingClientRect().height}px`;
            workspace.select(id);
        }}><Icon size={16} strokeWidth={1.8} aria-hidden="true" />{expanded ? "Collapse" : "Expand"}</button>;
    return <><div ref={inline} className="ui-preview-slot" />{createPortal(
        <div id={id} className="ui-preview-content" data-expanded={expanded || undefined} role="group" aria-label={title} onKeyDown={(event) => {
            if (expanded && event.key === "Escape" && !event.defaultPrevented) {
                event.preventDefault(); event.stopPropagation(); collapse();
            }
        }}>{children(toggle)}</div>, host)}</>;
}
