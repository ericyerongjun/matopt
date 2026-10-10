import assert from "node:assert/strict";
import { describe, it, beforeEach } from "node:test";
import {
    DEFAULT_UI_FONT_SIZE_PX,
    MAX_UI_FONT_SIZE_PX,
    MIN_UI_FONT_SIZE_PX,
    UI_FONT_SIZE_STORAGE_KEY,
    applyUiFontSizePx,
    loadUiFontSizePx,
    normalizeUiFontSizePx,
    setUiFontSizePx,
} from "./uiFontSize.ts";

/** Records every `setProperty` call so we can assert the write set exactly. */
const writes: Array<[string, string]> = [];
const inlineStyle: Record<string, string> = {};

(globalThis as any).document = {
    documentElement: {
        style: {
            setProperty(name: string, value: string) {
                writes.push([name, value]);
                inlineStyle[name] = value;
            },
        },
    },
};

const storage = new Map<string, string>();
(globalThis as any).localStorage = {
    getItem: (key: string) => (storage.has(key) ? storage.get(key)! : null),
    setItem: (key: string, value: string) => void storage.set(key, value),
};

beforeEach(() => {
    writes.length = 0;
    storage.clear();
    for (const key of Object.keys(inlineStyle)) delete inlineStyle[key];
});

describe("normalizeUiFontSizePx", () => {
    it("clamps to the 12-20px range", () => {
        assert.equal(normalizeUiFontSizePx(1), MIN_UI_FONT_SIZE_PX);
        assert.equal(normalizeUiFontSizePx(99), MAX_UI_FONT_SIZE_PX);
    });

    it("rounds and defaults", () => {
        assert.equal(normalizeUiFontSizePx(15.6), 16);
        assert.equal(normalizeUiFontSizePx(undefined), DEFAULT_UI_FONT_SIZE_PX);
        assert.equal(normalizeUiFontSizePx(Number.NaN), DEFAULT_UI_FONT_SIZE_PX);
        assert.equal(normalizeUiFontSizePx("16"), DEFAULT_UI_FONT_SIZE_PX);
    });
});

describe("applyUiFontSizePx", () => {
    it("writes the scalar and nothing else", () => {
        applyUiFontSizePx(17);
        assert.deepEqual(writes, [["--ui-font-size", "17px"]]);
    });

    it("never touches the root font-size (the defect this mechanism exists to avoid)", () => {
        applyUiFontSizePx(20);
        assert.equal(inlineStyle["font-size"], undefined);
        assert.equal((inlineStyle as any).fontSize, undefined);
        assert.equal(writes.length, 1);
        assert.equal(writes[0][0], "--ui-font-size");
    });

    it("normalizes before writing, so an out-of-range value cannot reach CSS", () => {
        applyUiFontSizePx(500);
        assert.deepEqual(writes, [["--ui-font-size", "20px"]]);
    });
});

describe("persistence", () => {
    it("stores under matopt.uiFontSizePx and applies", () => {
        setUiFontSizePx(18);
        assert.equal(storage.get(UI_FONT_SIZE_STORAGE_KEY), "18");
        assert.deepEqual(writes, [["--ui-font-size", "18px"]]);
    });

    it("persists the clamped value, not the raw one", () => {
        assert.equal(setUiFontSizePx(3), MIN_UI_FONT_SIZE_PX);
        assert.equal(storage.get(UI_FONT_SIZE_STORAGE_KEY), "12");
    });

    it("loads the persisted value, defaulting when absent or corrupt", () => {
        assert.equal(loadUiFontSizePx(), DEFAULT_UI_FONT_SIZE_PX);
        storage.set(UI_FONT_SIZE_STORAGE_KEY, "16");
        assert.equal(loadUiFontSizePx(), 16);
        storage.set(UI_FONT_SIZE_STORAGE_KEY, "not-a-number");
        assert.equal(loadUiFontSizePx(), DEFAULT_UI_FONT_SIZE_PX);
        storage.set(UI_FONT_SIZE_STORAGE_KEY, "999");
        assert.equal(loadUiFontSizePx(), MAX_UI_FONT_SIZE_PX);
    });
});
