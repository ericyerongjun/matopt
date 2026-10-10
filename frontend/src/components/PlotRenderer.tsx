/**
 * PlotRenderer — renders Plotly charts client-side from JSON data.
 *
 * Accepts a JSON string containing { data, layout? } in Plotly format
 * and renders an interactive chart using react-plotly.js.
 */

import React, { useEffect, useMemo, useRef, lazy, Suspense, memo } from "react";
import { mergeChartDefaults, plotlyDefaults, useChartTheme } from "./chartTheme.ts";
import { observePlotResize } from "./plotResize.ts";
import "./PlotRenderer.css";

// Lazy-load Plotly to avoid bloating the initial bundle (~3MB)
let plotlyRuntime: typeof import("plotly.js/dist/plotly").default | null = null;
const Plot = lazy(async () => {
    const [component, runtime] = await Promise.all([import("react-plotly.js"), import("plotly.js/dist/plotly")]);
    plotlyRuntime = runtime.default;
    return { default: component.default };
});

interface Props {
    /** JSON string with Plotly { data, layout?, config? } */
    json?: string;
    /** Already-parsed figure from a python-tool artifact. */
    figure?: { data?: Plotly.Data[]; layout?: Partial<Plotly.Layout>; config?: Partial<Plotly.Config> };
}

const DEFAULT_CONFIG: Partial<Plotly.Config> = {
    responsive: true,
    displayModeBar: true,
    modeBarButtonsToRemove: ["lasso2d", "select2d", "sendChartToCloud"],
    showSendToCloud: false,
    displaylogo: false,
};

function PlotRenderer({ json, figure }: Props) {
    const theme = useChartTheme();
    const containerRef = useRef<HTMLDivElement>(null);
    const graphRef = useRef<HTMLElement | null>(null);
    const resizeCleanupRef = useRef<(() => void) | undefined>(undefined);
    const parsed = useMemo(() => {
        try {
            const obj = figure ?? (json ? JSON.parse(json) : null);
            if (!obj) return null;
            return {
                data: obj.data || [],
                layout: mergeChartDefaults(plotlyDefaults(theme), obj.layout),
                config: { ...DEFAULT_CONFIG, ...obj.config },
            };
        } catch {
            return null;
        }
    }, [json, figure, theme]);
    const autoWidth = parsed?.layout.autosize !== false && !(typeof parsed?.layout.width === "number" && parsed.layout.width > 0);
    const bindResize = (graph: HTMLElement) => {
        resizeCleanupRef.current?.();
        graphRef.current = graph;
        resizeCleanupRef.current = autoWidth && containerRef.current && plotlyRuntime
            ? observePlotResize(containerRef.current, graph, (element) => plotlyRuntime!.Plots.resize(element)) : undefined;
    };
    useEffect(() => {
        if (graphRef.current) bindResize(graphRef.current);
        return () => resizeCleanupRef.current?.();
    }, [autoWidth]);

    if (!parsed || parsed.data.length === 0) {
        return (
            <div className="plot-error">
                Failed to render plot — invalid data.
            </div>
        );
    }

    return (
        <div className="plot-container" ref={containerRef}>
            <Suspense
                fallback={
                    <div className="plot-loading">
                        Loading chart...
                    </div>
                }
            >
                <Plot
                    data={parsed.data}
                    layout={parsed.layout}
                    config={parsed.config}
                    useResizeHandler
                    onInitialized={(_figure, graph) => bindResize(graph)}
                    onPurge={() => { resizeCleanupRef.current?.(); graphRef.current = null; }}
                    style={{
                        width: typeof parsed.layout.width === "number" && parsed.layout.width > 0 ? parsed.layout.width : "100%",
                        height: typeof parsed.layout.height === "number" && parsed.layout.height > 0 ? parsed.layout.height : 380,
                    }}
                />
            </Suspense>
        </div>
    );
}

export default memo(PlotRenderer);
