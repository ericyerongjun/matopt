/**
 * PlotRenderer — renders Plotly charts client-side from JSON data.
 *
 * Accepts a JSON string containing { data, layout? } in Plotly format
 * and renders an interactive chart using react-plotly.js.
 */

import React, { useMemo, lazy, Suspense, memo } from "react";

// Lazy-load Plotly to avoid bloating the initial bundle (~3MB)
const Plot = lazy(() => import("react-plotly.js"));

interface Props {
    /** JSON string with Plotly { data, layout?, config? } */
    json: string;
}

const DEFAULT_LAYOUT: Partial<Plotly.Layout> = {
    autosize: true,
    margin: { l: 50, r: 30, t: 40, b: 50 },
    font: { family: "inherit", size: 14 },
    paper_bgcolor: "transparent",
    plot_bgcolor: "#fafafa",
    xaxis: { gridcolor: "#e6e6f0", zeroline: false },
    yaxis: { gridcolor: "#e6e6f0", zeroline: false },
    legend: { orientation: "h", y: -0.15 },
    colorway: ["#10a37f", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6"],
};

const DEFAULT_CONFIG: Partial<Plotly.Config> = {
    responsive: true,
    displayModeBar: true,
    modeBarButtonsToRemove: ["lasso2d", "select2d"],
    displaylogo: false,
};

function PlotRenderer({ json }: Props) {
    const parsed = useMemo(() => {
        try {
            const obj = JSON.parse(json);
            return {
                data: obj.data || [],
                layout: { ...DEFAULT_LAYOUT, ...obj.layout },
                config: { ...DEFAULT_CONFIG, ...obj.config },
            };
        } catch {
            return null;
        }
    }, [json]);

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
