/** Apply ZCode theme classes while preserving MatOpt's saved preference. */

/** A user-facing preference. `'system'` is resolved, never applied directly. */
export type Theme = "light" | "dark" | "system";

/** The concrete schemes a preference can resolve to. */
export type ResolvedTheme = "light" | "dark";

/** localStorage key holding the persisted preference. */
export const THEME_STORAGE_KEY = "matopt.theme";

/** Default preference for a first visit. */
export const DEFAULT_THEME: Theme = "system";

/** Compatibility attribute for the pre-paint canvas and saved MatOpt settings. */
export const DARK_ATTRIBUTE = "data-mo-dark-theme";

/** Root attribute publishing the preference for shells that mirror it. */
export const THEME_SOURCE_ATTRIBUTE = "data-mo-theme-source";

const THEMES: readonly Theme[] = ["light", "dark", "system"];

/** Narrow an arbitrary value to a Theme. */
export function isTheme(value: unknown): value is Theme {
    return typeof value === "string" && (THEMES as readonly string[]).includes(value);
}

/**
 * Resolve a preference to a concrete scheme. `'system'` follows the OS via
 * `prefers-color-scheme`; `matchMedia` is absent in SSR and some test
 * environments, so a missing implementation resolves to light rather than
 * throwing.
 */
export function resolveTheme(theme: Theme): ResolvedTheme {
    if (theme === "light" || theme === "dark") return theme;
    return typeof matchMedia !== "undefined" && matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
}

/** Read the persisted preference. Storage can throw; fall back to the default. */
export function loadTheme(): Theme {
    try {
        const raw = localStorage.getItem(THEME_STORAGE_KEY);
        return isTheme(raw) ? raw : DEFAULT_THEME;
    } catch {
        return DEFAULT_THEME;
    }
}

/**
 * Project a preference onto the document. Also publishes
 * `html[data-mo-theme-source]` — `'system'` while the preference is `'system'`,
 * otherwise the resolved scheme — so a shell can tell "dark because the user
 * chose it" from "dark because the OS is dark".
 */
export function applyTheme(theme: Theme): void {
    if (typeof document === "undefined") return;
    const scheme = resolveTheme(theme);
    const root = document.documentElement;
    root.style.colorScheme = scheme;
    root.classList.toggle("dark", scheme === "dark");
    root.classList.toggle("theme-zai-dark", scheme === "dark");
    root.classList.toggle("theme-zai-light", scheme === "light");
    root.setAttribute(THEME_SOURCE_ATTRIBUTE, theme === "system" ? "system" : scheme);
    // Keep the pre-paint canvas in sync with the native theme classes.
    root.toggleAttribute(DARK_ATTRIBUTE, scheme === "dark");
    const body = document.body;
    if (!body) return;
    body.toggleAttribute(DARK_ATTRIBUTE, scheme === "dark");
}

/** Persist a preference and apply it. */
export function setTheme(theme: Theme): void {
    try {
        localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
        /* private mode — the theme still applies for this session */
    }
    applyTheme(theme);
}

/**
 * Follow OS scheme changes, but only while the preference is `'system'` — a
 * user who picked light or dark explicitly must not be overridden when the OS
 * flips at sunset.
 * @returns an unsubscribe function.
 */
export function watchSystemTheme(getTheme: () => Theme, onChange?: (scheme: ResolvedTheme) => void): () => void {
    if (typeof matchMedia === "undefined") return () => {};
    const query = matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
        if (getTheme() !== "system") return;
        applyTheme("system");
        onChange?.(resolveTheme("system"));
    };
    query.addEventListener("change", handler);
    return () => query.removeEventListener("change", handler);
}

/** Boot entry point: apply the persisted preference before first paint. */
export function bootTheme(): Theme {
    const theme = loadTheme();
    applyTheme(theme);
    return theme;
}
