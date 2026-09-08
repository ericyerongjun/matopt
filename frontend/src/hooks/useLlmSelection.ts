/**
 * Load provider catalog and remember provider + model + reasoning + verify.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchProviders } from "../services/providers";
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
    const [provider, setProvider] = useState<ProviderId | null>(null);
    const [model, setModel] = useState<string | null>(null);
    const [reasoning, setReasoning] = useState<ReasoningLevel>("medium");
    const [verify, setVerify] = useState(false);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let active = true;
        fetchProviders()
            .then((data) => {
                if (!active) return;
                setCatalog(data);
                const stored = readStored();
                const storedProvider = data.providers.find((p) => p.id === stored.provider && p.configured);
                const fallback = data.providers.find((p) => p.configured);
                const nextProvider =
                    storedProvider ??
                    data.providers.find((p) => p.id === data.default_provider && p.configured) ??
                    fallback ??
                    null;
                const nextModelId =
                    (nextProvider && nextProvider.models.some((m) => m.id === stored.model) && stored.model) ||
                    nextProvider?.default_model ||
                    data.default_model ||
                    null;
                const spec = nextProvider?.models.find((m) => m.id === nextModelId);
                const allowed = spec?.reasoning_levels ?? data.levels.map((l) => l.id);
                const nextReasoning: ReasoningLevel =
                    (stored.reasoning && allowed.includes(stored.reasoning) && stored.reasoning) ||
                    spec?.default_reasoning ||
                    data.default_reasoning ||
                    "medium";
                setProvider(nextProvider?.id ?? null);
                setModel(nextModelId);
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
            setProvider(nextProvider);
            setModel(nextModel);
            const spec = catalog?.providers.find((p) => p.id === nextProvider)?.models.find((m) => m.id === nextModel);
            const allowed = spec?.reasoning_levels;
            let nextReasoning = reasoning;
            if (allowed && !allowed.includes(reasoning)) {
                nextReasoning = spec?.default_reasoning ?? "medium";
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
        setReasoning(level);
        writeStored({ [REASONING_KEY]: level });
    }, []);

    const selectVerify = useCallback((value: boolean) => {
        setVerify(value);
        writeStored({ [VERIFY_KEY]: value ? "1" : "0" });
    }, []);

    const selectedLabel = useMemo(() => {
        if (!catalog || !provider || !model) return "Select model";
        const group = catalog.providers.find((p) => p.id === provider);
        const option = group?.models.find((m) => m.id === model);
        return option?.label ?? model;
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
