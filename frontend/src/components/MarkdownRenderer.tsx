/** MatOpt web host adapter for the complete, pinned ZCode rendering stack. */
import { lazy, memo, Suspense, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { MessageResponse } from "../zcode/components/ai-elements/message.js";
import type { Theme } from "../zcode/useTheme.js";
import { preprocessLaTeX } from "../utils/formulaHelpers.ts";
import { normalizeAgentMarkdown } from "../utils/markdownNormalize.ts";
import { prepareWorkspaceMarkdown } from "../zcode/adapters/workspaceMarkdown.ts";
import { RichSourcePreview } from "./RichSourcePreview.tsx";
import { closedRichFences, richFence, richFenceKey, standaloneRichSource } from "../utils/richSource.ts";

const PlotRenderer = lazy(() => import("./PlotRenderer.tsx"));
const ChartRenderer = lazy(() => import("./ChartRenderer.tsx"));

function renderScientificFence(code: string, language: string): ReactNode | undefined {
    const normalized = language.toLowerCase();
    if (normalized !== "plotly" && normalized !== "chartjs" && normalized !== "chart") return undefined;
    return (
        <Suspense fallback={<div className="p-4 text-ui-sm text-foreground-subtle">Loading chart…</div>}>
            {normalized === "plotly" ? <PlotRenderer json={code} /> : <ChartRenderer json={code} />}
        </Suspense>
    );
}

function openWebUrl(url: string) {
    const target = new URL(url, window.location.href);
    if (target.protocol === "http:" || target.protocol === "https:") {
        window.open(target.href, "_blank", "noopener,noreferrer");
    }
}

function currentTheme(): Theme {
    return typeof document !== "undefined" && document.documentElement.classList.contains("dark") ? "dark" : "light";
}

interface Props {
    content: string;
    conversationId?: string | null;
    streaming?: boolean;
}

function MarkdownRenderer({ content, conversationId, streaming = false }: Props) {
    const [theme, setTheme] = useState<Theme>(currentTheme);
    useEffect(() => {
        const observer = new MutationObserver(() => setTheme(currentTheme()));
        observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
        return () => observer.disconnect();
    }, []);
    const markdown = useMemo(() => preprocessLaTeX(normalizeAgentMarkdown(prepareWorkspaceMarkdown(
        content,
        conversationId,
        typeof window === "undefined" ? undefined : window.location.origin,
    ))), [content, conversationId]);
    const publishing = streaming;
    const closedFences = useMemo(() => closedRichFences(markdown), [markdown]);
    const standaloneKind = useMemo(() => standaloneRichSource(content), [content]);
    const renderFence = useCallback((code: string, language: string): ReactNode | undefined => {
        const rich = richFence(language);
        if (rich) return <RichSourcePreview source={code} kind={rich.kind} defaultView={rich.defaultView}
            complete={!publishing && closedFences.has(richFenceKey(language, code))} embedded />;
        return renderScientificFence(code, language);
    }, [publishing, closedFences]);

    return (
        <div aria-busy={streaming} data-response-streaming={streaming ? "true" : "false"}>
        {standaloneKind ? <RichSourcePreview source={content} kind={standaloneKind} defaultView="preview" complete={!publishing} /> :
        <MessageResponse
            sessionId={conversationId ?? undefined}
            streaming={publishing}
            theme={theme}
            onOpenExternalUrl={openWebUrl}
            renderCodeContent={renderFence}
        >
            {markdown}
        </MessageResponse>}
        </div>
    );
}

export default memo(MarkdownRenderer);
