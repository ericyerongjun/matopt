import "../../../test/jsdom-env.mjs";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { ZCodeIntlProvider, useZCodeIntl } from "./IntlProvider.tsx";

function Label() { return <span>{useZCodeIntl().intl.formatMessage({ id: "common.loading" })}</span>; }

test("a catalog that is not loaded yet arrives after mounting and replaces the fallback", async () => {
    const host = document.createElement("div"); document.body.append(host);
    const root = createRoot(host);
    const { default: chinese } = await import("./locales/zh-CN.ts");
    await act(async () => {
        root.render(<ZCodeIntlProvider initialLocale="zh-CN"><Label /></ZCodeIntlProvider>);
        await new Promise((resolve) => setTimeout(resolve, 30));
    });
    assert.equal(host.textContent, chinese["common.loading"]);
    act(() => root.unmount());
    host.remove();
});

test("message catalogs stay out of the provider's static imports", () => {
    assert.doesNotMatch(readFileSync(new URL("./IntlProvider.tsx", import.meta.url), "utf8"), /^import .* from "\.\/locales\//m);
});
