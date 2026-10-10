import { lazy, memo, Suspense, useEffect, useId, useMemo, useState, type ReactNode } from "react";
import katex from "katex";
import { ChevronDown } from "lucide-react";
import type { JSONValue, UINode, UISurface } from "../../types/ui.ts";
import MarkdownRenderer from "../../components/MarkdownRenderer.tsx";
import { hasBindings, resolveBindings } from "./model.ts";
import { useUiWorkspace, type SurfaceRecord } from "./UiWorkspace.tsx";
import { TableOverflow } from "./TableOverflow.tsx";

const PlotRenderer = lazy(() => import("../../components/PlotRenderer.tsx"));
const MermaidBlock = lazy(() => import("../../zcode/components/ai-elements/mermaid-block.tsx").then((module) => ({ default: module.MermaidBlock })));
const text = (value: JSONValue | undefined, fallback = ""): string => value === undefined || value === null ? fallback : typeof value === "string" ? value : typeof value === "object" ? JSON.stringify(value) : String(value);
const numeric = (value: JSONValue | undefined, fallback?: number): number | undefined => typeof value === "number" && Number.isFinite(value) ? value : fallback;

export function NativeSurface({ record }: { record: SurfaceRecord }) {
    return <TableOverflow label={`${record.surface.title} data table`}><div className="ui-native-surface">{record.surface.nodes.map((node) => <SurfaceNode key={node.id} node={node} record={record} />)}</div></TableOverflow>;
}

function SurfaceNode({ node, record }: { node: UINode; record: SurfaceRecord }) {
    const workspace = useUiWorkspace();
    const controlId = useId();
    const bound = useMemo(() => hasBindings(node.props), [node.props]);
    const props = useMemo(() => bound ? resolveBindings(node.props, record.state) as Record<string, JSONValue> : node.props, [bound, node.props, record.state]);
    const label = text(props.label, node.id);
    const disabled = record.surface.status !== "committed" || record.busy;
    const stateKey = text(node.props.stateKey);
    const value = record.state[stateKey] ?? props.value;
    const children = node.children?.map((child) => <SurfaceNode key={child.id} node={child} record={record} />);
    const set = (next: JSONValue) => { if (stateKey) workspace.updateState(record.surface.id, { [stateKey]: next }); };
    const description = typeof props.description === "string" ? <span className="ui-control-description" id={`${controlId}-description`}>{props.description}</span> : null;
    const shared = { id: controlId, disabled: disabled || !stateKey, "aria-describedby": description ? `${controlId}-description` : undefined };
    switch (node.type) {
        case "markdown": return <div data-ui-node={node.id}><MarkdownRenderer content={text(props.text)} conversationId={workspace.conversationId} /></div>;
        case "equation": return <div className="ui-equation" data-ui-node={node.id} aria-label={text(props.label, text(props.latex))}
            dangerouslySetInnerHTML={{ __html: katex.renderToString(text(props.latex), { displayMode: props.inline !== true, throwOnError: false, trust: false, strict: "ignore", output: "htmlAndMathml" }) }} />;
        case "stack": return <div data-ui-node={node.id} className={`ui-node-stack ${props.direction === "horizontal" ? "ui-node-row" : ""}`}>{children}</div>;
        case "grid": return <div data-ui-node={node.id} className="ui-node-grid" style={{ "--ui-grid-columns": Math.max(1, Math.min(4, numeric(props.columns, 2)!)) } as React.CSSProperties}>{children}</div>;
        case "comparison": return <Comparison node={node} props={props} />;
        case "table": return <SurfaceTable node={node} props={props} state={record.state} />;
        case "plot": return <SurfacePlot nodeId={node.id} props={props} />;
        case "mermaid": return <div className="ui-surface-diagram" data-ui-node={node.id}><Suspense fallback={<pre>{text(props.code)}</pre>}><MermaidBlock code={text(props.code)} /></Suspense></div>;
        case "tabs": return <SurfaceTabs node={node} record={record} />;
        case "disclosure": return <details className="ui-disclosure" data-ui-node={node.id}
            open={typeof record.viewState[node.id] === "boolean" ? record.viewState[node.id] as boolean : props.defaultOpen === true}
            onToggle={(event) => workspace.updateViewState(record.surface.id, node.id, event.currentTarget.open)}><summary><span>{label}</span><ChevronDown size={20} strokeWidth={1.8} aria-hidden="true" /></summary><div>{children}</div></details>;
        case "slider": return <div className="ui-control" data-ui-node={node.id}>
            <label htmlFor={controlId}>{label}<output htmlFor={controlId}>{text(value)}{props.unit ? ` ${text(props.unit)}` : ""}</output></label>
            <input {...shared} type="range" min={numeric(props.min, 0)} max={numeric(props.max, 100)} step={numeric(props.step, 1)} value={numeric(value, 0)} onChange={(event) => set(Number(event.target.value))} />{description}
        </div>;
        case "number": return <NumberControl nodeId={node.id} label={label} unit={text(props.unit)} value={numeric(value, 0)!}
            min={numeric(props.min)} max={numeric(props.max)} step={numeric(props.step, 1)} description={description} shared={shared} onValueChange={set} />;
        case "select": {
            const options = Array.isArray(props.options) ? props.options : [];
            const normalized = options.map((option) => option && typeof option === "object" && !Array.isArray(option) ? { value: option.value ?? option.label, label: text(option.label, text(option.value)) } : { value: option, label: text(option) });
            const selected = normalized.findIndex((option) => JSON.stringify(option.value) === JSON.stringify(value));
            return <div className="ui-control" data-ui-node={node.id}><label htmlFor={controlId}>{label}</label>
                <div className="ui-select"><select {...shared} value={selected < 0 ? "" : String(selected)} onChange={(event) => { const option = normalized[Number(event.target.value)]; if (option?.value !== undefined) set(option.value); }}>
                    {selected < 0 && <option value="" disabled>Choose an option</option>}
                    {normalized.map((option, index) => <option key={index} value={String(index)}>{option.label}</option>)}
                </select><ChevronDown size={20} strokeWidth={1.8} aria-hidden="true" /></div>{description}</div>;
        }
        case "toggle": return <div className="ui-control" data-ui-node={node.id}><label className="ui-toggle" htmlFor={controlId}>
            <input {...shared} type="checkbox" checked={value === true} onChange={(event) => set(event.target.checked)} /><span>{label}</span>
        </label>{description}</div>;
        case "button": {
            const actionId = text(props.actionId);
            const action = record.surface.actions.find((item) => item.id === actionId);
            return <button type="button" className="ui-action-button" data-ui-node={node.id} disabled={disabled || workspace.conversationBusy || !action || !workspace.conversationId}
                onClick={() => workspace.runAction(record.surface.id, actionId)}>{text(props.label, action?.label ?? "Compute")}</button>;
        }
        default: return null;
    }
}

const SurfacePlot = memo(function SurfacePlot({ nodeId, props }: { nodeId: string; props: Record<string, JSONValue> }) {
    const figure = useMemo(() => {
        const allowed = new Set(["scatter", "bar", "histogram", "box", "heatmap", "contour", "surface", "pie", "violin", "scatter3d", "histogram2d", "histogram2dcontour", "waterfall", "funnel", "indicator"]);
        const data = Array.isArray(props.data) ? props.data.filter((trace) => trace && typeof trace === "object" && !Array.isArray(trace) && (!trace.type || allowed.has(String(trace.type)))) : [];
        const rawLayout = props.layout && typeof props.layout === "object" && !Array.isArray(props.layout) ? props.layout : {};
        const { images: _images, mapbox: _mapbox, map: _map, geo: _geo, ...layout } = rawLayout;
        return { data: data as unknown as Plotly.Data[], layout: layout as Partial<Plotly.Layout>, config: { displaylogo: false, showSendToCloud: false } };
    }, [props]);
    return <figure className="ui-surface-plot" data-ui-node={nodeId}>
        {props.label && <figcaption>{text(props.label)}</figcaption>}
        <Suspense fallback={<p className="ui-surface-note" role="status">Loading chart…</p>}><PlotRenderer figure={figure} /></Suspense>
        {props.caption && <figcaption className="ui-surface-note">{text(props.caption)}</figcaption>}
    </figure>;
});

function NumberControl({ nodeId, label, unit, value, min, max, step, description, shared, onValueChange }: {
    nodeId: string; label: string; unit: string; value: number; min?: number; max?: number; step?: number; description: ReactNode;
    shared: { id: string; disabled: boolean; "aria-describedby"?: string }; onValueChange: (value: number) => void;
}) {
    const [draft, setDraft] = useState(String(value));
    const [focused, setFocused] = useState(false);
    useEffect(() => { if (!focused) setDraft(String(value)); }, [value, focused]);
    const valid = draft.trim() !== "" && Number.isFinite(Number(draft)) && (min === undefined || Number(draft) >= min) && (max === undefined || Number(draft) <= max);
    return <div className="ui-control" data-ui-node={nodeId}>
        <label htmlFor={shared.id}>{label}{unit && <span className="ui-control-unit">{unit}</span>}</label>
        <input {...shared} type="number" min={min} max={max} step={step} value={focused ? draft : String(value)} aria-invalid={focused && draft !== "" && !valid || undefined}
            onFocus={() => { setDraft(String(value)); setFocused(true); }} onBlur={() => setFocused(false)}
            onChange={(event) => {
                const raw = event.target.value; setDraft(raw);
                if (raw.trim() === "") return;
                const next = Number(raw);
                if (Number.isFinite(next) && (min === undefined || next >= min) && (max === undefined || next <= max)) onValueChange(next);
            }} />{description}
    </div>;
}

function Comparison({ node, props }: { node: UINode; props: Record<string, JSONValue> }) {
    const items = Array.isArray(props.items) ? props.items : [];
    return <div className="ui-comparison" data-ui-node={node.id}>{items.map((item, index) => {
        const values = item && typeof item === "object" && !Array.isArray(item) ? item : { label: String(index + 1), value: item };
        return <div className="ui-comparison-item" key={text(values.id, String(index))}><span className="ui-surface-note">{text(values.label)}</span>
            <strong>{text(values.value)}</strong>{values.detail && <span>{text(values.detail)}</span>}</div>;
    })}</div>;
}

function SurfaceTable({ node, props, state }: { node: UINode; props: Record<string, JSONValue>; state: Record<string, JSONValue> }) {
    const columns = (Array.isArray(props.columns) ? props.columns : []).map((column, index) => column && typeof column === "object" && !Array.isArray(column)
        ? { key: text(column.key, String(index)), label: text(column.label, text(column.key)) } : { key: text(column), label: text(column) });
    const rows = Array.isArray(props.rows) ? props.rows : [];
    const filter = text(state[text(props.filterKey)]).toLocaleLowerCase();
    const filtered = filter ? rows.filter((row) => text(row).toLocaleLowerCase().includes(filter)) : rows;
    return <div className="ui-native-table" data-ui-node={node.id}><div className="ui-table-scroll" tabIndex={0} role="region" aria-label={text(props.label, "Data table")}>
        <table className="ui-table">{props.caption && <caption>{text(props.caption)}</caption>}<thead><tr>{columns.map((column, index) => <th key={`${column.key}-${index}`} scope="col">{column.label}</th>)}</tr></thead>
            <tbody>{filtered.slice(0, 1000).map((row, index) => <tr key={index}>{columns.map((column, columnIndex) => <td key={`${column.key}-${columnIndex}`}>{text(Array.isArray(row) ? row[columnIndex] : row && typeof row === "object" ? row[column.key] : row)}</td>)}</tr>)}</tbody>
        </table>
        {!filtered.length && <p className="ui-surface-note">No matching rows.</p>}
        {filtered.length > 1000 && <p className="ui-surface-note">Showing 1,000 of {filtered.length.toLocaleString()} rows. Export the result for all data.</p>}
    </div></div>;
}

function SurfaceTabs({ node, record }: { node: UINode; record: SurfaceRecord }) {
    const nodes = node.children ?? [];
    const workspace = useUiWorkspace();
    const active = record.viewState[node.id] ?? nodes[0]?.id;
    const setActive = (value: string) => workspace.updateViewState(record.surface.id, node.id, value);
    const prefix = useId();
    const selected = nodes.find((child) => child.id === active) ?? nodes[0];
    return <div className="ui-tabs" data-ui-node={node.id}>
        <div role="tablist" aria-label={text(node.props.label, "Views")} onKeyDown={(event) => {
            const index = nodes.findIndex((child) => child.id === selected?.id);
            let next: number | null = null;
            if (event.key === "ArrowRight") next = (index + 1) % nodes.length;
            if (event.key === "ArrowLeft") next = (index - 1 + nodes.length) % nodes.length;
            if (event.key === "Home") next = 0;
            if (event.key === "End") next = nodes.length - 1;
            if (next !== null && nodes[next]) { event.preventDefault(); setActive(nodes[next].id); document.getElementById(`${prefix}-tab-${next}`)?.focus(); }
        }}>
            {nodes.map((child, index) => <button type="button" key={child.id} id={`${prefix}-tab-${index}`} role="tab" aria-selected={child.id === selected?.id}
                aria-controls={`${prefix}-panel-${index}`} tabIndex={child.id === selected?.id ? 0 : -1} onClick={() => setActive(child.id)}>{text(child.props.label, `View ${index + 1}`)}</button>)}
        </div>
        {nodes.map((child, index) => <div key={child.id} id={`${prefix}-panel-${index}`} role="tabpanel" aria-labelledby={`${prefix}-tab-${index}`} hidden={child.id !== selected?.id}>
            <SurfaceNode node={child} record={record} />
        </div>)}
    </div>;
}

export function hasActionButton(surface: UISurface, actionId: string): boolean {
    const contains = (nodes: UINode[]): boolean => nodes.some((node) => node.type === "button" && node.props.actionId === actionId || contains(node.children ?? []));
    return contains(surface.nodes);
}
