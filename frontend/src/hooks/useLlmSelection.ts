/** Load the fixed model and remember verification and the demo profile. */
import { useCallback, useEffect, useState } from "react";
import { fetchProviders } from "../services/providers.ts";

const VERIFY_KEY = "matopt.verify";
const ULTRAFAST_KEY = "matopt.ultrafast";

function readPreference(key: string, fallback: boolean): boolean {
    try {
        // The old effort picker also stored ultrafast=0 when selecting a level.
        // Migrate that retired configuration to the new default once.
        if (key === ULTRAFAST_KEY && localStorage.getItem("matopt.reasoning") !== null) return true;
        const value = localStorage.getItem(key);
        return value === "1" ? true : value === "0" ? false : fallback;
    } catch {
        return fallback;
    }
}

function writePreference(key: string, value: boolean) {
    try { localStorage.setItem(key, value ? "1" : "0"); } catch { /* private mode */ }
}

export function useLlmSelection() {
    const [selectedLabel, setSelectedLabel] = useState("DeepSeek V4.1 Flash");
    const [verify, setVerify] = useState(() => readPreference(VERIFY_KEY, false));
    const [ultrafast, setUltrafast] = useState(() => readPreference(ULTRAFAST_KEY, true));
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let active = true;
        // Old thinking-level selections must never override the model default.
        try {
            if (localStorage.getItem("matopt.reasoning") !== null) localStorage.setItem(ULTRAFAST_KEY, "1");
            localStorage.removeItem("matopt.reasoning");
            localStorage.setItem("matopt.provider", "deepseek");
            localStorage.setItem("matopt.model", "deepseek-flash");
        } catch { /* private mode */ }
        fetchProviders()
            .then((data) => {
                if (!active) return;
                const model = data.providers.find((p) => p.id === "deepseek")?.models.find((m) => m.id === "deepseek-flash");
                if (model) setSelectedLabel(model.label);
            })
            .catch((err: unknown) => {
                if (active) setError(err instanceof Error ? err.message : "Failed to load models");
            })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, []);

    const selectUltrafast = useCallback((value: boolean) => {
        setUltrafast(value);
        writePreference(ULTRAFAST_KEY, value);
    }, []);
    const selectVerify = useCallback((value: boolean) => {
        setVerify(value);
        writePreference(VERIFY_KEY, value);
    }, []);

    return {
        provider: "deepseek",
        model: "deepseek-flash",
        // The server applies the demo override or the configured model default.
        reasoning: null,
        ultrafast,
        verify,
        selectedLabel,
        loading,
        error,
        selectUltrafast,
        selectVerify,
    };
}
