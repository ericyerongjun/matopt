/**
 * Load provider catalog and remember provider + model + reasoning + verify.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchProviders } from "../services/providers.ts";
import type {
    ProviderCatalog,
    ProviderId,
    ProviderModel,
    ReasoningLevel,
} from "../types/protocol";

const PROVIDER_KEY = "matopt.provider";
const MODEL_KEY = "matopt.model";
const REASONING_KEY = "matopt.reasoning";
const VERIFY_KEY = "matopt.verify";

function readStored() {
    try {
        return {
            provider: localStorage.getItem(PROVIDER_KEY) as ProviderId | null,
            model: localStorage.getItem(MODEL_KEY),
            reasoning: localStorage.getItem(REASONING_KEY) as ReasoningLevel | null,
            verify: localStorage.getItem(VERIFY_KEY) === "1",
        };
    } catch {
        return { provider: null, model: null, reasoning: null, verify: false };
    }
}

function writeStored(partial: Record<string, string>) {
    try {
        for (const [key, value] of Object.entries(partial)) localStorage.setItem(key, value);
    } catch {
        /* private mode */
    }
}

export function useLlmSelection() {
    const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
    const [provider, setProvider] = useState<ProviderId | null>("deepseek");
    const [model, setModel] = useState<string | null>("deepseek-flash");
    const [reasoning, setReasoning] = useState<ReasoningLevel>("high");
    const [verify, setVerify] = useState(false);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let active = true;
        fetchProviders()
            .then((data) => {
                if (!active) return;
                const flash = data.providers.find((p) => p.id === "deepseek");
                setCatalog({ ...data, providers: flash ? [{ ...flash, models: flash.models.filter((m) => m.id === "deepseek-flash") }] : [] });
                const stored = readStored();
                const spec = flash?.models.find((m) => m.id === "deepseek-flash");
                const allowed = spec?.reasoning_levels ?? ["off", "low", "high", "max"];
                const nextReasoning: ReasoningLevel =
                    (stored.reasoning && allowed.includes(stored.reasoning) && stored.reasoning) ||
                    spec?.default_reasoning ||
                    "high";
                setProvider("deepseek");
                setModel("deepseek-flash");
                writeStored({ [PROVIDER_KEY]: "deepseek", [MODEL_KEY]: "deepseek-flash", [REASONING_KEY]: nextReasoning });
                setReasoning(nextReasoning);
                setVerify(stored.verify);
            })
            .catch((err: unknown) => {
                if (!active) return;
                setError(err instanceof Error ? err.message : "Failed to load models");
            })
            .finally(() => {
                if (!active) return;
                setLoading(false);
            });
        return () => {
            active = false;
        };
    }, []);

    const modelSpec: ProviderModel | null = useMemo(() => {
        if (!catalog || !provider || !model) return null;
        return catalog.providers.find((p) => p.id === provider)?.models.find((m) => m.id === model) ?? null;
    }, [catalog, provider, model]);

    const allowedLevels = useMemo(() => {
        const order = catalog?.levels.map((l) => l.id) ?? ["off", "low", "medium", "high", "max"];
        const allowed = new Set(modelSpec?.reasoning_levels ?? order);
        return (catalog?.levels ?? []).filter((l) => allowed.has(l.id));
    }, [catalog, modelSpec]);

    const select = useCallback(
        (nextProvider: ProviderId, nextModel: string) => {
            if (nextProvider !== "deepseek" || nextModel !== "deepseek-flash") return;
            setProvider(nextProvider);
            setModel(nextModel);
            const spec = catalog?.providers.find((p) => p.id === nextProvider)?.models.find((m) => m.id === nextModel);
            const allowed = spec?.reasoning_levels;
            let nextReasoning = reasoning;
            if (allowed && !allowed.includes(reasoning)) {
                nextReasoning = spec?.default_reasoning ?? "high";
                setReasoning(nextReasoning);
            }
            writeStored({
                [PROVIDER_KEY]: nextProvider,
                [MODEL_KEY]: nextModel,
                [REASONING_KEY]: nextReasoning,
            });
        },
        [catalog, reasoning]
    );

    const selectReasoning = useCallback((level: ReasoningLevel) => {
        if (!(modelSpec?.reasoning_levels ?? ["off", "low", "high", "max"]).includes(level)) return;
        setReasoning(level);
        writeStored({ [REASONING_KEY]: level });
    }, [modelSpec]);

    const selectVerify = useCallback((value: boolean) => {
        setVerify(value);
        writeStored({ [VERIFY_KEY]: value ? "1" : "0" });
    }, []);

    const selectedLabel = useMemo(() => {
        if (!catalog || !provider || !model) return "DeepSeek V4.1 Flash";
        const group = catalog.providers.find((p) => p.id === provider);
        const option = group?.models.find((m) => m.id === model);
        return option?.label ?? "DeepSeek V4.1 Flash";
    }, [catalog, provider, model]);

    return {
        catalog,
        provider,
        model,
        modelSpec,
        reasoning,
        verify,
        allowedLevels,
        selectedLabel,
        loading,
        error,
        select,
        selectReasoning,
        selectVerify,
    };
}
