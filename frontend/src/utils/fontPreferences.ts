/** Persist the reader's font choices as overrides of the three font tokens. */

export type FontRole = "reading" | "interface" | "code";

/** A family name, or `null` for MatOpt's bundled default. */
export type FontPreferences = Record<FontRole, string | null>;

export const FONT_PREFERENCES_STORAGE_KEY = "matopt.fonts";

export const DEFAULT_FONT_PREFERENCES: FontPreferences = { reading: null, interface: null, code: null };

/** Each role's token, bundled default and the generic stack every choice falls back to. */
export const FONT_ROLES: Record<FontRole, { variable: string; label: string; description: string; defaultFamily: string; fallback: string }> = {
    reading: {
        variable: "--font-serif", label: "Reading", description: "Answers, your requests and titles",
        defaultFamily: "Newsreader Variable", fallback: '"Iowan Old Style", "Charter", Georgia, "Songti SC", serif',
    },
    interface: {
        variable: "--font-sans", label: "Interface", description: "Menus, buttons, labels and tables",
        defaultFamily: "Inter Variable", fallback: 'ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji"',
    },
    code: {
        variable: "--font-mono", label: "Code", description: "Code, file paths and tool calls",
        defaultFamily: "IBM Plex Mono", fallback: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "PingFang SC", "Microsoft YaHei", monospace',
    },
};

/** Widely installed families offered without asking for font access. */
export const POPULAR_FONTS: Record<FontRole, string[]> = {
    reading: ["Inter Variable", "Georgia", "Charter", "Iowan Old Style", "Palatino", "Baskerville", "Times New Roman", "Cambria", "Helvetica Neue", "Arial"],
    interface: ["Newsreader Variable", "system-ui", "Helvetica Neue", "Arial", "Avenir Next", "Segoe UI", "Verdana", "Georgia"],
    code: ["SF Mono", "Menlo", "Monaco", "Consolas", "Cascadia Code", "Courier New"],
};

/** Keep only characters a family name needs, so a stored value cannot escape the CSS value. */
export function sanitizeFontFamily(value: unknown): string | null {
    if (typeof value !== "string") return null;
    const name = value.replace(/["'\\;{}<>()]/g, "").replace(/\s+/g, " ").trim().slice(0, 80);
    return name || null;
}

export function normalizeFontPreferences(value: unknown): FontPreferences {
    const source = value && typeof value === "object" ? value as Partial<Record<FontRole, unknown>> : {};
    return {
        reading: sanitizeFontFamily(source.reading),
        interface: sanitizeFontFamily(source.interface),
        code: sanitizeFontFamily(source.code),
    };
}

/** The CSS value for a role: the chosen family first, then the role's generic stack. */
export function fontStack(role: FontRole, family: string | null): string {
    const { defaultFamily, fallback } = FONT_ROLES[role];
    const name = sanitizeFontFamily(family) ?? defaultFamily;
    // CSS generic keywords must stay unquoted to keep their meaning.
    const head = name === "system-ui" ? name : `"${name}"`;
    return `${head}, ${fallback}`;
}

/** Storage can throw (private mode); the defaults apply instead. */
export function loadFontPreferences(): FontPreferences {
    try {
        const raw = localStorage.getItem(FONT_PREFERENCES_STORAGE_KEY);
        return normalizeFontPreferences(raw ? JSON.parse(raw) : null);
    } catch {
        return { ...DEFAULT_FONT_PREFERENCES };
    }
}

/** Write only the overridden tokens; a default choice removes its override. */
export function applyFontPreferences(preferences: FontPreferences): void {
    const rootStyle = typeof document === "undefined" ? undefined : document.documentElement?.style;
    if (!rootStyle?.setProperty) return;
    for (const role of Object.keys(FONT_ROLES) as FontRole[]) {
        const family = sanitizeFontFamily(preferences[role]);
        if (family) rootStyle.setProperty(FONT_ROLES[role].variable, fontStack(role, family));
        else rootStyle.removeProperty?.(FONT_ROLES[role].variable);
    }
}

export function setFontPreferences(preferences: FontPreferences): FontPreferences {
    const next = normalizeFontPreferences(preferences);
    try {
        localStorage.setItem(FONT_PREFERENCES_STORAGE_KEY, JSON.stringify(next));
    } catch {
        /* private mode — the fonts still apply for this session */
    }
    applyFontPreferences(next);
    return next;
}

/** Mirror a change made in another tab. */
export function subscribeToFontPreferenceStorageChanges(): () => void {
    const handleStorage = (event: StorageEvent) => {
        if (event.key !== FONT_PREFERENCES_STORAGE_KEY) return;
        try { applyFontPreferences(normalizeFontPreferences(event.newValue ? JSON.parse(event.newValue) : null)); }
        catch { applyFontPreferences(DEFAULT_FONT_PREFERENCES); }
    };
    window.addEventListener("storage", handleStorage);
    return () => window.removeEventListener("storage", handleStorage);
}

export function bootFontPreferences(): FontPreferences {
    const preferences = loadFontPreferences();
    applyFontPreferences(preferences);
    return preferences;
}

type LocalFontData = { family: string };
type LocalFontWindow = Window & { queryLocalFonts?: () => Promise<LocalFontData[]> };

/** Whether this browser can list installed fonts (Chromium's Local Font Access API). */
export function canListLocalFonts(): boolean {
    return typeof window !== "undefined" && typeof (window as LocalFontWindow).queryLocalFonts === "function";
}

/** Installed font families, sorted and de-duplicated. The browser asks the user first. */
export async function listLocalFontFamilies(): Promise<string[]> {
    const query = (window as LocalFontWindow).queryLocalFonts;
    if (!query) return [];
    const fonts = await query.call(window);
    return [...new Set(fonts.map((font) => font.family).filter(Boolean))].sort((a, b) => a.localeCompare(b));
}
