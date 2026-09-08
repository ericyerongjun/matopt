/**
 * PlotRenderer — renders Plotly charts client-side from JSON data.
 *
 * Accepts a JSON string containing { data, layout? } in Plotly format
 * and renders an interactive chart using react-plotly.js.
 */

import React, { useMemo, lazy, Suspense, memo } from "react";
import "./PlotRenderer.css";

// Lazy-load Plotly to avoid bloating the initial bundle (~3MB)
const Plot = lazy(() => import("react-plotly.js"));

interface Props {
    /** JSON string with Plotly { data, layout?, config? } */
    json?: string;
    /** Already-parsed figure from a python-tool artifact. */
    figure?: { data?: Plotly.Data[]; layout?: Partial<Plotly.Layout>; config?: Partial<Plotly.Config> };
}

const DEFAULT_LAYOUT: Partial<Plotly.Layout> = {
    autosize: true,
    margin: { l: 50, r: 30, t: 40, b: 50 },
    font: { family: "inherit", size: 14, color: "#0d0d0d" },
    paper_bgcolor: "transparent",
    plot_bgcolor: "#f7f7f8",
    xaxis: { gridcolor: "#e5e5e5", zeroline: false, color: "#6e6e80" },
    yaxis: { gridcolor: "#e5e5e5", zeroline: false, color: "#6e6e80" },
    legend: { orientation: "h", y: -0.15 },
    colorway: ["#10a37f", "#0d0d0d", "#4d6bfe", "#f5a623", "#6e6e80"],
};

const DEFAULT_CONFIG: Partial<Plotly.Config> = {
    responsive: true,
    displayModeBar: true,
    modeBarButtonsToRemove: ["lasso2d", "select2d"],
    displaylogo: false,
};

function PlotRenderer({ json, figure }: Props) {
    const parsed = useMemo(() => {
        try {
            const obj = figure ?? (json ? JSON.parse(json) : null);
            if (!obj) return null;
            return {
                data: obj.data || [],
                layout: { ...DEFAULT_LAYOUT, ...obj.layout },
                config: { ...DEFAULT_CONFIG, ...obj.config },
            };
        } catch {
            return null;
        }
    }, [json, figure]);

    if (!parsed || parsed.data.length === 0) {
        return (
            <div className="plot-error">
                Failed to render plot — invalid data.
            </div>
        );
    }

    return (
        <div className="plot-container">
            <Suspense
                fallback={
                    <div style={{ padding: 24, textAlign: "center" }}>
                        Loading chart...
                    </div>
                }
            >
                <Plot
                    data={parsed.data}
                    layout={parsed.layout}
                    config={parsed.config}
                    useResizeHandler
                    style={{ width: "100%", height: "380px" }}
                />
            </Suspense>
        </div>
    );
}

export default memo(PlotRenderer);
