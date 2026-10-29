import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";
import {
    FONT_PREFERENCES_STORAGE_KEY,
    applyFontPreferences,
    fontStack,
    loadFontPreferences,
    normalizeFontPreferences,
    sanitizeFontFamily,
    setFontPreferences,
} from "./fontPreferences.ts";

const inlineStyle = new Map<string, string>();
(globalThis as any).document = {
    documentElement: {
        style: {
            setProperty(name: string, value: string) { inlineStyle.set(name, value); },
            removeProperty(name: string) { inlineStyle.delete(name); },
        },
    },
};
const storage = new Map<string, string>();
(globalThis as any).localStorage = {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => { storage.set(key, value); },
};

beforeEach(() => { inlineStyle.clear(); storage.clear(); });

describe("font preferences", () => {
    it("strips characters that could break out of a CSS value", () => {
        assert.equal(sanitizeFontFamily('Evil"; } body { color: red'), "Evil body color: red");
        assert.equal(sanitizeFontFamily("   "), null);
        assert.equal(sanitizeFontFamily(42), null);
        assert.equal(sanitizeFontFamily("x".repeat(200))?.length, 80);
    });

    it("puts the chosen family before the role's generic fallback", () => {
        assert.match(fontStack("reading", "Georgia"), /^"Georgia", .*serif$/);
        assert.match(fontStack("interface", "system-ui"), /^system-ui, /);
        assert.match(fontStack("code", null), /^"IBM Plex Mono", .*monospace$/);
    });

    it("overrides only chosen roles and clears an override set back to default", () => {
        applyFontPreferences({ reading: "Charter", interface: null, code: "Menlo" });
        assert.deepEqual([...inlineStyle.keys()].sort(), ["--font-mono", "--font-serif"]);
        applyFontPreferences({ reading: null, interface: null, code: "Menlo" });
        assert.deepEqual([...inlineStyle.keys()], ["--font-mono"]);
    });

    it("persists, reloads and tolerates corrupt storage", () => {
        setFontPreferences({ reading: "Palatino", interface: null, code: null });
        assert.deepEqual(loadFontPreferences(), { reading: "Palatino", interface: null, code: null });
        storage.set(FONT_PREFERENCES_STORAGE_KEY, "{not json");
        assert.deepEqual(loadFontPreferences(), { reading: null, interface: null, code: null });
        assert.deepEqual(normalizeFontPreferences({ reading: 3, extra: "x" }), { reading: null, interface: null, code: null });
    });
});
