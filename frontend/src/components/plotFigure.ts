/** Plotly fetches these by itself, so a model-written figure could use them as a request to any host. */
const REMOTE_LAYOUT_KEYS = ["images", "mapbox", "map", "geo"];
const REMOTE_CONFIG_KEYS = ["plotlyServerURL", "topojsonURL", "mapboxAccessToken", "showSendToCloud", "showEditInChartStudio", "showLink", "sendData", "linkText"];

type Bag = Record<string, unknown>;
const isBag = (value: unknown): value is Bag => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const without = (source: Bag, keys: string[]): Bag => Object.fromEntries(Object.entries(source).filter(([key]) => !keys.includes(key)));

function localLayout(layout: Bag): Bag {
    const result = without(layout, REMOTE_LAYOUT_KEYS);
    // Templates are merged into the layout by Plotly, so they can carry the same keys.
    if (isBag(result.template) && isBag(result.template.layout)) {
        result.template = { ...result.template, layout: without(result.template.layout, REMOTE_LAYOUT_KEYS) };
    }
    return result;
}

/** Applied to every figure source: surfaces, chat fences and tool artifacts. */
export function localOnlyFigure<Figure extends { layout?: unknown; config?: unknown }>(figure: Figure): Figure {
    return {
        ...figure,
        layout: isBag(figure.layout) ? localLayout(figure.layout) : figure.layout,
        config: isBag(figure.config) ? without(figure.config, REMOTE_CONFIG_KEYS) : figure.config,
    };
}
