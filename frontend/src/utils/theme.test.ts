import assert from "node:assert/strict";
import { describe, it, beforeEach } from "node:test";
import {
    DARK_ATTRIBUTE,
    DEFAULT_THEME,
    THEME_SOURCE_ATTRIBUTE,
    THEME_STORAGE_KEY,
    applyTheme,
    isTheme,
    loadTheme,
    resolveTheme,
    setTheme,
    watchSystemTheme,
} from "./theme.ts";

/** Minimal element double: enough for attribute writes and inline style. */
function makeElement() {
    const attrs = new Set<string>();
    const classes = new Set<string>();
    const style: Record<string, string> = {};
    return {
        style,
        attrs,
        classes,
        classList: { toggle: (name: string, force: boolean) => { if (force) classes.add(name); else classes.delete(name); } },
        setAttribute: (name: string) => void attrs.add(name),
        removeAttribute: (name: string) => void attrs.delete(name),
        toggleAttribute: (name: string, force?: boolean) => {
            if (force) attrs.add(name);
            else attrs.delete(name);
        },
        hasAttribute: (name: string) => attrs.has(name),
    };
}

const html = makeElement();
const body = makeElement();
(globalThis as any).document = { documentElement: html, body };

const storage = new Map<string, string>();
(globalThis as any).localStorage = {
    getItem: (key: string) => (storage.has(key) ? storage.get(key)! : null),
    setItem: (key: string, value: string) => void storage.set(key, value),
};

/** Controllable prefers-color-scheme. */
let systemDark = false;
const mediaListeners = new Set<() => void>();
(globalThis as any).matchMedia = (query: string) => ({
    matches: query.includes("dark") ? systemDark : false,
    addEventListener: (_: string, listener: () => void) => void mediaListeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => void mediaListeners.delete(listener),
});

function flipSystem(dark: boolean) {
    systemDark = dark;
    for (const listener of [...mediaListeners]) listener();
}

beforeEach(() => {
    storage.clear();
    html.attrs.clear();
    html.classes.clear();
    body.attrs.clear();
    mediaListeners.clear();
    systemDark = false;
    delete html.style.colorScheme;
    delete html.style.fontSize;
});

describe("resolveTheme", () => {
    it("passes explicit preferences through", () => {
        assert.equal(resolveTheme("light"), "light");
        assert.equal(resolveTheme("dark"), "dark");
    });

    it("follows prefers-color-scheme for system", () => {
        systemDark = true;
        assert.equal(resolveTheme("system"), "dark");
        systemDark = false;
        assert.equal(resolveTheme("system"), "light");
    });
});

describe("applyTheme", () => {
    it("dark: sets the palette attribute on body and html, plus color-scheme", () => {
        applyTheme("dark");
        assert.ok(body.hasAttribute(DARK_ATTRIBUTE));
        assert.ok(html.hasAttribute(DARK_ATTRIBUTE));
        assert.equal(html.style.colorScheme, "dark");
        assert.ok(html.classes.has("dark") && html.classes.has("theme-zai-dark"));
        assert.equal(html.classes.has("theme-zai-light"), false);
        assert.equal(html.attrs.has(THEME_SOURCE_ATTRIBUTE), true);
    });

    it("light: retracts the palette attribute again", () => {
        applyTheme("dark");
        applyTheme("light");
        assert.equal(body.hasAttribute(DARK_ATTRIBUTE), false);
        assert.equal(html.hasAttribute(DARK_ATTRIBUTE), false);
        assert.equal(html.style.colorScheme, "light");
        assert.ok(html.classes.has("theme-zai-light"));
        assert.equal(html.classes.has("theme-zai-dark"), false);
    });

    it("system: resolves then publishes 'system' as the source", () => {
        systemDark = true;
        applyTheme("system");
        assert.ok(body.hasAttribute(DARK_ATTRIBUTE));
        // Reads the attribute set, not the value; just assert it was written.
        assert.ok(html.attrs.has(THEME_SOURCE_ATTRIBUTE));
    });
});

describe("watchSystemTheme", () => {
    it("re-applies when the OS flips and the preference is system", () => {
        applyTheme("system");
        assert.equal(body.hasAttribute(DARK_ATTRIBUTE), false);
        watchSystemTheme(() => "system");
        flipSystem(true);
        assert.ok(body.hasAttribute(DARK_ATTRIBUTE));
    });

    it("does NOT override an explicit light/dark choice", () => {
        applyTheme("light");
        watchSystemTheme(() => "light");
        flipSystem(true);
        assert.equal(body.hasAttribute(DARK_ATTRIBUTE), false);
        assert.equal(html.style.colorScheme, "light");
        assert.ok(html.classes.has("theme-zai-light"));
        assert.equal(html.classes.has("theme-zai-dark"), false);
    });
});

describe("persistence", () => {
    it("defaults to system and rejects unknown stored values", () => {
        assert.equal(loadTheme(), DEFAULT_THEME);
        storage.set(THEME_STORAGE_KEY, "dark");
        assert.equal(loadTheme(), "dark");
        storage.set(THEME_STORAGE_KEY, "solarized");
        assert.equal(loadTheme(), DEFAULT_THEME);
    });

    it("stores under matopt.theme and applies", () => {
        setTheme("dark");
        assert.equal(storage.get(THEME_STORAGE_KEY), "dark");
        assert.ok(body.hasAttribute(DARK_ATTRIBUTE));
    });

    it("isTheme narrows", () => {
        assert.ok(isTheme("light") && isTheme("dark") && isTheme("system"));
        assert.equal(isTheme("solarized"), false);
        assert.equal(isTheme(null), false);
    });
});
