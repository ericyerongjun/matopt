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
    const parsed = useMemo(() => {
        try {
            const obj = JSON.parse(json);
            if (!obj?.type || !obj?.data) return null;
            return {
                type: obj.type,
                data: obj.data,
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            position: "bottom",
                            labels: { boxWidth: 12, boxHeight: 12 },
                        },
                        title: obj.options?.plugins?.title,
                    },
                    scales: obj.options?.scales,
                    ...obj.options,
                },
                height: typeof obj.height === "number" ? obj.height : 360,
            };
        } catch {
            return null;
        }
    }, [json]);

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
                    <div style={{ padding: 24, textAlign: "center" }}>
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
