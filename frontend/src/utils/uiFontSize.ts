/** Persist ZCode's native --ui-font-size scalar without scaling rem-based layout. */

/** localStorage key holding the persisted choice. */
export const UI_FONT_SIZE_STORAGE_KEY = "matopt.uiFontSizePx";

/** Un-persisted default, and the value `--ui-font-size` is declared with. */
export const DEFAULT_UI_FONT_SIZE_PX = 14;

/** Inclusive native ZCode interface font range. */
export const MIN_UI_FONT_SIZE_PX = 12;
export const MAX_UI_FONT_SIZE_PX = 20;

/** The single custom property this module is allowed to write. */
const UI_FONT_SIZE_VARIABLE = "--ui-font-size";

/** Clamp, round, and fall back to the default for anything non-numeric. */
export function normalizeUiFontSizePx(value: unknown): number {
    return typeof value === "number" && Number.isFinite(value)
        ? Math.min(MAX_UI_FONT_SIZE_PX, Math.max(MIN_UI_FONT_SIZE_PX, Math.round(value)))
        : DEFAULT_UI_FONT_SIZE_PX;
}

/**
 * Read the persisted size. Storage can throw (Safari private mode, disabled
 * cookies), in which case the default is used rather than failing the boot.
 */
export function loadUiFontSizePx(): number {
    try {
        const raw = localStorage.getItem(UI_FONT_SIZE_STORAGE_KEY);
        return normalizeUiFontSizePx(raw === null ? undefined : Number(raw));
    } catch {
        return DEFAULT_UI_FONT_SIZE_PX;
    }
}

/**
 * Write the scalar. Normalizes first, so an out-of-range or non-numeric input
 * can never reach CSS. A no-op outside the browser.
 */
export function applyUiFontSizePx(fontSizePx: number): void {
    const rootStyle = typeof document === "undefined" ? undefined : document.documentElement?.style;
    if (!rootStyle?.setProperty) return;
    rootStyle.setProperty(UI_FONT_SIZE_VARIABLE, `${normalizeUiFontSizePx(fontSizePx)}px`);
}

/** Persist the choice and apply it. */
export function setUiFontSizePx(fontSizePx: number): number {
    const next = normalizeUiFontSizePx(fontSizePx);
    try {
        localStorage.setItem(UI_FONT_SIZE_STORAGE_KEY, String(next));
    } catch {
        /* private mode — the size still applies for this session */
    }
    applyUiFontSizePx(next);
    return next;
}

/**
 * Follow changes made in another tab. `localStorage` events do not fire in the
 * tab that wrote the value, so this only ever mirrors a foreign write.
 * @returns an unsubscribe function.
 */
export function subscribeToUiFontSizeStorageChanges(): () => void {
    const handleStorage = (event: StorageEvent) => {
        if (event.key !== UI_FONT_SIZE_STORAGE_KEY) return;
        applyUiFontSizePx(event.newValue === null ? DEFAULT_UI_FONT_SIZE_PX : Number(event.newValue));
    };
    window.addEventListener("storage", handleStorage);
    return () => window.removeEventListener("storage", handleStorage);
}

/** Boot entry point: apply the persisted size before first paint. */
export function bootUiFontSize(): number {
    const size = loadUiFontSizePx();
    applyUiFontSizePx(size);
    return size;
}
