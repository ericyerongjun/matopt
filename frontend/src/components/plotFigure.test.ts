import assert from "node:assert/strict";
import { test } from "node:test";
import { interopDefault, localOnlyFigure } from "./plotFigure.ts";

test("every figure loses the layout and config keys that make Plotly contact another host", () => {
    const figure = localOnlyFigure({
        data: [{ x: [1], y: [2] }],
        layout: { title: "Kept", images: [{ source: "https://evil.example/?q=secret" }], mapbox: { style: "https://evil.example/s" }, map: {}, geo: {},
            template: { data: {}, layout: { font: { size: 12 }, images: [{ source: "https://evil.example/t" }] } } },
        config: { responsive: false, plotlyServerURL: "https://evil.example", topojsonURL: "https://evil.example/", showSendToCloud: true, mapboxAccessToken: "x" },
    });
    assert.deepEqual(figure.layout, { title: "Kept", template: { data: {}, layout: { font: { size: 12 } } } });
    assert.deepEqual(figure.config, { responsive: false });
    assert.deepEqual(figure.data, [{ x: [1], y: [2] }]);
});

test("a figure without layout or config passes through", () => {
    const bare: { data: unknown[]; layout?: unknown; config?: unknown } = { data: [] };
    assert.deepEqual(localOnlyFigure(bare), { data: [], layout: undefined, config: undefined });
});

test("interopDefault unwraps a bundled CommonJS default and leaves a component alone", () => {
    const component = () => null;
    assert.equal(interopDefault({ default: component }), component);
    assert.equal(interopDefault(component), component);
});
