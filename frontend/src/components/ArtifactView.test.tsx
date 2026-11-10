import "../../test/jsdom-env.mjs";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ArtifactView, artifactImageAlt } from "./ArtifactView.tsx";

let root: Root | null = null;
afterEach(() => { act(() => root?.unmount()); root = null; document.body.replaceChildren(); });

test("figures are described by their own name", () => {
    assert.equal(artifactImageAlt({ name: "residual_plot-v2.png" }), "Figure: residual plot v2");
    assert.equal(artifactImageAlt({ name: "" }), "Generated figure");
    const host = document.createElement("div"); document.body.append(host); root = createRoot(host);
    act(() => root?.render(<ArtifactView artifact={{ kind: "image", name: "loss_curve.png", url: "/api/files/c/loss_curve.png" }} conversationId="c" />));
    assert.equal(host.querySelector("img")?.getAttribute("alt"), "Figure: loss curve");
});
