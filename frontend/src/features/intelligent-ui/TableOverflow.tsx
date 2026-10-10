import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { resolveMarkdownTableVirtualScrollMax } from "../../zcode/components/ai-elements/markdown-table.tsx";

interface TableViewport {
    key: string;
    table: HTMLTableElement;
    viewport: HTMLElement;
    mount: HTMLDivElement;
    refresh: () => void;
}

function scrollLimit({ table, viewport }: TableViewport): number {
    const nativeLimit = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
    const track = table.closest('[data-markdown-table-frame]')?.querySelector('[data-markdown-table-virtual-scroll-track]');
    const trackWidth = track?.getBoundingClientRect().width ?? 0;
    const tableWidth = table.getBoundingClientRect().width;
    // The pinned Markdown renderer uses the outer track, including its frame border.
    // Use its own limit so the last arrow does not advertise an unreachable 2px step.
    return trackWidth > 0 && tableWidth > 0 ? Math.min(nativeLimit, resolveMarkdownTableVirtualScrollMax({ tableWidth, trackWidth })) : nativeLimit;
}

/** Enhance the existing native and pinned Markdown table scrollers without replacing either renderer. */
export function TableOverflow({ children, label }: { children: ReactNode; label: string }) {
    const root = useRef<HTMLDivElement>(null);
    const [tables, setTables] = useState<TableViewport[]>([]);
    useEffect(() => {
        const element = root.current;
        if (!element) return;
        const entries = new Map<HTMLTableElement, TableViewport>();
        let nextKey = 0;
        const discover = () => {
            const next: TableViewport[] = [];
            for (const table of element.querySelectorAll<HTMLTableElement>('table.ui-table, table[data-streamdown="table"]')) {
                const viewport = table.closest<HTMLElement>('.ui-table-scroll') ?? table.parentElement;
                const anchor = table.closest<HTMLElement>('[data-markdown-table-frame]') ?? viewport;
                if (!viewport || !anchor?.parentElement) continue;
                let entry = entries.get(table);
                if (!entry) {
                    const mount = document.createElement('div');
                    mount.className = 'ui-table-overflow-mount';
                    anchor.before(mount);
                    entry = { key: String(nextKey++), table, viewport, mount, refresh: () => {} };
                    entries.set(table, entry);
                }
                entry.refresh();
                next.push(entry);
            }
            for (const [table, entry] of entries) if (!next.includes(entry)) { entry.mount.remove(); entries.delete(table); }
            setTables(current => current.length === next.length && current.every((entry, index) => entry === next[index]) ? current : next);
        };
        discover();
        // Progressive Markdown and filtered native tables can change without replacing their surface.
        const observer = new MutationObserver(discover);
        observer.observe(element, { childList: true, subtree: true, characterData: true });
        return () => { observer.disconnect(); for (const entry of entries.values()) entry.mount.remove(); };
    }, []);
    return <div ref={root} className="ui-table-overflow-scope">{children}{tables.map((entry, index) => createPortal(
        <TableScrollCue entry={entry} label={tables.length > 1 ? `${label} ${index + 1}` : label} />, entry.mount, entry.key,
    ))}</div>;
}

function TableScrollCue({ entry, label }: { entry: TableViewport; label: string }) {
    const descriptionId = useId();
    const [edges, setEdges] = useState({ overflow: false, left: false, right: false });
    const measuredEdges = useRef(edges);
    const move = (direction: "left" | "right" | "start" | "end", amount?: number) => {
        const { viewport } = entry;
        const max = scrollLimit(entry);
        const step = amount ?? Math.max(80, viewport.clientWidth * .75);
        viewport.scrollLeft = Math.max(0, Math.min(max, direction === "start" ? 0 : direction === "end" ? max : viewport.scrollLeft + (direction === "left" ? -step : step)));
        entry.refresh();
    };
    useLayoutEffect(() => {
        const { viewport, table } = entry;
        const owned = new Map<string, string>();
        const addFallback = (name: string, value: string) => {
            const current = viewport.getAttribute(name);
            if (current === null || owned.get(name) === current) { viewport.setAttribute(name, value); owned.set(name, value); }
            else owned.delete(name); // A renderer revision owns its current explicit value.
        };
        const descriptions = () => (viewport.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(value => value && value !== descriptionId);
        const restore = () => {
            for (const [name, value] of owned) if (viewport.getAttribute(name) === value) viewport.removeAttribute(name);
            owned.clear();
            const remaining = descriptions();
            if (remaining.length) viewport.setAttribute('aria-describedby', remaining.join(' ')); else viewport.removeAttribute('aria-describedby');
        };
        const measure = () => {
            if (!viewport.clientWidth) return; // Hidden Results/Files tabs keep their existing scroll state.
            const max = scrollLimit(entry);
            const overflow = max > 1;
            const next = { overflow, left: overflow && viewport.scrollLeft > 1, right: overflow && viewport.scrollLeft < max - 1 };
            const current = measuredEdges.current;
            if (current.overflow !== next.overflow || current.left !== next.left || current.right !== next.right) {
                measuredEdges.current = next;
                setEdges(next);
            }
            if (overflow) {
                addFallback('tabindex', '0');
                addFallback('role', 'region');
                addFallback('aria-label', table.caption?.textContent?.trim() || label);
                viewport.setAttribute('aria-describedby', [...descriptions(), descriptionId].join(' '));
            } else restore();
        };
        const keyDown = (event: KeyboardEvent) => {
            if (event.target !== viewport || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || scrollLimit(entry) <= 1) return;
            const direction = ({ ArrowLeft: 'left', ArrowRight: 'right', Home: 'start', End: 'end' } as const)[event.key as 'ArrowLeft' | 'ArrowRight' | 'Home' | 'End'];
            if (direction) { event.preventDefault(); move(direction, 48); }
        };
        entry.refresh = measure;
        const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
        observer?.observe(viewport); observer?.observe(table);
        viewport.addEventListener('scroll', measure, { passive: true });
        viewport.addEventListener('keydown', keyDown);
        window.addEventListener('resize', measure);
        measure();
        return () => {
            entry.refresh = () => {};
            observer?.disconnect();
            viewport.removeEventListener('scroll', measure);
            viewport.removeEventListener('keydown', keyDown);
            window.removeEventListener('resize', measure);
            restore();
        };
    }, [entry, label, descriptionId]);
    if (!edges.overflow) return null;
    return <div className="ui-table-overflow-cue">
        <span id={descriptionId}>Scroll for more columns</span>
        <div className="ui-table-overflow-actions">
            <button type="button" aria-label="Scroll table left" disabled={!edges.left} onClick={() => move('left')}><ChevronLeft size={16} aria-hidden="true" /></button>
            <button type="button" aria-label="Scroll table right" disabled={!edges.right} onClick={() => move('right')}><ChevronRight size={16} aria-hidden="true" /></button>
        </div>
    </div>;
}
