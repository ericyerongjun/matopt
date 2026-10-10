/**
 * ChartRenderer — renders Chart.js charts client-side from JSON data.
 *
 * Accepts a JSON string containing:
 * {
 *   "type": "line" | "bar" | "pie" | ...,
 *   "data": { ... },
 *   "options"?: { ... },
 *   "height"?: number
 * }
 */

import React, { useMemo, lazy, Suspense, memo } from "react";
import { chartJsDefaults, mergeChartDefaults, themedChartData, useChartTheme } from "./chartTheme.ts";
import "./ChartRenderer.css";

const Chart = lazy(async () => {
    await import("chart.js/auto");
    const mod = await import("react-chartjs-2");
    return { default: mod.Chart };
});

interface Props {
    /** JSON string with Chart.js { type, data, options?, height? } */
    json: string;
}

function ChartRenderer({ json }: Props) {
    const theme = useChartTheme();
    const parsed = useMemo(() => {
        try {
            const obj = JSON.parse(json);
            if (!obj?.type || !obj?.data) return null;
            return {
                type: obj.type,
                data: themedChartData(obj.data, obj.type, theme),
                options: mergeChartDefaults(chartJsDefaults(theme, obj.type, obj.options), obj.options),
                height: typeof obj.height === "number" ? obj.height : 360,
            };
        } catch {
            return null;
        }
    }, [json, theme]);

    if (!parsed) {
        return (
            <div className="chart-error">
                Failed to render chart — invalid data.
            </div>
        );
    }

    return (
        <div className="chart-container" style={{ height: parsed.height }}>
            <Suspense
                fallback={
                    <div className="chart-loading">
                        Loading chart...
                    </div>
                }
            >
                <Chart
                    type={parsed.type}
                    data={parsed.data}
                    options={parsed.options}
                />
            </Suspense>
        </div>
    );
}

export default memo(ChartRenderer);
