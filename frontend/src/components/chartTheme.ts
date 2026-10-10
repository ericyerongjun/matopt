/** MatOpt figure defaults resolved from the active, directly copied ZCode theme. */
import { useEffect, useState } from "react";
import type { ChartData, ChartOptions, ChartType } from "chart.js";

export interface ChartTheme {
  foreground?: string;
  subtle?: string;
  background?: string;
  surface?: string;
  border?: string;
  family?: string;
  fontSize?: number;
  colorway: string[];
}

/** Chart engines do not consistently parse CSS variables or modern color spaces. */
function engineColor(value: string): string | undefined {
  if (!value) return undefined;
  if (!/^(?:oklch|oklab|color\(|color-mix\()/i.test(value)) return value;
  try {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) return undefined;
    context.fillStyle = value;
    context.fillRect(0, 0, 1, 1);
    const [red, green, blue, alpha] = context.getImageData(0, 0, 1, 1).data;
    return `rgba(${red}, ${green}, ${blue}, ${alpha / 255})`;
  } catch { return undefined; }
}

export function readChartTheme(): ChartTheme {
  if (typeof document === "undefined") return { colorway: [] };
  const root = document.documentElement;
  const computed = getComputedStyle(root);
  const color = (name: string) => engineColor(computed.getPropertyValue(name).trim());
  return {
    foreground: color("--color-foreground") || computed.color || undefined,
    subtle: color("--color-foreground-subtle"),
    background: color("--color-background"),
    surface: color("--color-surface"),
    border: color("--color-border"),
    family: getComputedStyle(document.body || root).fontFamily || computed.fontFamily || undefined,
    fontSize: Number.parseFloat(computed.getPropertyValue("--ui-font-size")) || undefined,
    colorway: Array.from({ length: 6 }, (_, index) => color(`--color-usage-chart-${index + 1}`)).filter((value): value is string => Boolean(value)),
  };
}

export function useChartTheme(): ChartTheme {
  const [theme, setTheme] = useState<ChartTheme>({ colorway: [] });
  useEffect(() => {
    const refresh = () => setTheme((previous) => {
      const next = readChartTheme();
      return JSON.stringify(previous) === JSON.stringify(next) ? previous : next;
    });
    refresh();
    const observer = new MutationObserver(refresh);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style"] });
    return () => observer.disconnect();
  }, []);
  return theme;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** Defaults fill omitted fields; explicit false, null, arrays and figure options win. */
export function mergeChartDefaults<T>(defaults: T, supplied: unknown): T {
  if (supplied === undefined) return defaults;
  if (!isObject(defaults) || !isObject(supplied)) return supplied as T;
  const result: Record<string, unknown> = { ...defaults };
  for (const [key, value] of Object.entries(supplied)) {
    if (value !== undefined) result[key] = mergeChartDefaults(result[key], value);
  }
  return result as T;
}

export function plotlyDefaults(theme: ChartTheme): Partial<Plotly.Layout> {
  const axis = { gridcolor: theme.border, zeroline: false, color: theme.subtle || theme.foreground };
  return {
    autosize: true,
    margin: { l: 50, r: 30, t: 40, b: 50 },
    font: { family: theme.family, size: theme.fontSize, color: theme.foreground },
    paper_bgcolor: theme.background,
    plot_bgcolor: theme.background,
    xaxis: axis,
    yaxis: { ...axis },
    legend: { orientation: "h", y: -0.15 },
    ...(theme.colorway.length && { colorway: theme.colorway }),
  };
}

export function chartJsDefaults(theme: ChartTheme, type: ChartType, options?: ChartOptions): ChartOptions {
  const radial = type === "radar" || type === "polarArea";
  const axes = radial ? { r: {} } : type === "pie" || type === "doughnut" ? {} : { x: {}, y: {} };
  const suppliedScales = isObject(options?.scales) ? options.scales : {};
  const scaleIds = Object.keys(suppliedScales).length ? Object.keys(suppliedScales) : Object.keys(axes);
  const scales = Object.fromEntries(scaleIds.map((id) => [id, {
    ticks: { color: theme.subtle || theme.foreground }, grid: { color: theme.border }, border: { color: theme.border },
    title: { color: theme.foreground }, ...(radial && { pointLabels: { color: theme.foreground } }),
  }]));
  return {
    responsive: true, maintainAspectRatio: false, color: theme.foreground,
    font: { family: theme.family, size: theme.fontSize },
    plugins: { legend: { position: "bottom", labels: { boxWidth: 12, boxHeight: 12, color: theme.foreground } }, title: { color: theme.foreground } },
    scales,
  };
}

export function themedChartData(data: ChartData, type: ChartType, theme: ChartTheme): ChartData {
  if (!Array.isArray(data.datasets) || !theme.colorway.length) return data;
  const segmented = ["pie", "doughnut", "polarArea"].includes(type);
  return { ...data, datasets: data.datasets.map((dataset, index) => {
    if (!isObject(dataset)) return dataset;
    const colors = segmented && Array.isArray(dataset.data) ? dataset.data.map((_, item) => theme.colorway[item % theme.colorway.length]) : theme.colorway[index % theme.colorway.length];
    return mergeChartDefaults({ ...dataset, backgroundColor: colors, borderColor: colors }, dataset);
  }) };
}
