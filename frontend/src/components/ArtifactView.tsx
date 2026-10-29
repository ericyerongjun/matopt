/** Conversation artifacts share one renderer in chat and the optional canvas. */
import { useEffect, useState, type ReactNode } from "react";
import { Expand } from "lucide-react";
import PlotRenderer from "./PlotRenderer.tsx";
import type { Artifact } from "../types/protocol.ts";
import { useOptionalUiWorkspace } from "../features/intelligent-ui/UiWorkspace.tsx";
import MarkdownRenderer from "./MarkdownRenderer.tsx";
import { RichSourcePreview } from "./RichSourcePreview.tsx";
import { MAX_RICH_SOURCE_LENGTH, type RichSourceKind } from "../utils/richSource.ts";

function fileUrl(conversationId: string | null | undefined, artifact: Artifact): string | undefined {
    const candidate = artifact.url || (conversationId && artifact.path ? `/api/files/${encodeURIComponent(conversationId)}/${artifact.path.split("/").map(encodeURIComponent).join("/")}` : undefined);
    if (!candidate) return undefined;
    try { const parsed = new URL(candidate, typeof location === "undefined" ? "http://localhost" : location.origin); return ["http:", "https:", "blob:"].includes(parsed.protocol) ? candidate : undefined; }
    catch { return undefined; }
}

/** Rich files are read as data from this conversation. Active content is only
 * ever sent to the isolated runtime, never inserted into the host document. */
export function safeHtmlArtifactSource(url: string | undefined, conversationId?: string | null, origin = typeof location === "undefined" ? "http://localhost" : location.origin): string | null {
    if (!url || !conversationId) return null;
    try {
        const parsed = new URL(url, origin);
        return parsed.origin === origin && parsed.pathname.startsWith(`/api/files/${encodeURIComponent(conversationId)}/`) ? parsed.href : null;
    } catch { return null; }
}

function richArtifactKind(artifact: Artifact): RichSourceKind | null {
    const mime = artifact.mime?.split(";")[0].trim().toLowerCase();
    const file = (artifact.path || artifact.name).toLowerCase();
    if (artifact.kind === "svg" || mime === "image/svg+xml" || /\.svg$/.test(file)) return "svg";
    if (["html", "html_file"].includes(artifact.kind) || mime === "text/html" || /\.html?$/.test(file)) return "html";
    if (artifact.kind === "markdown" || mime === "text/markdown" || /\.(?:md|markdown)$/.test(file)) return "markdown";
    return null;
}

async function readRichArtifact(response: Response): Promise<string> {
    if (Number(response.headers.get("content-length")) > MAX_RICH_SOURCE_LENGTH) throw new Error("This file is too large to preview.");
    if (!response.body) return "";
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let source = "";
    let bytes = 0;
    try {
        for (;;) {
            const { done, value } = await reader.read();
            if (done) return source + decoder.decode();
            bytes += value.byteLength;
            if (bytes > MAX_RICH_SOURCE_LENGTH) { await reader.cancel(); throw new Error("This file is too large to preview."); }
            source += decoder.decode(value, { stream: true });
        }
    } finally { reader.releaseLock(); }
}

export function ArtifactView({ artifact, conversationId, inCanvas = false }: { artifact: Artifact; conversationId?: string | null; inCanvas?: boolean }) {
    const url = fileUrl(conversationId, artifact);
    const workspace = useOptionalUiWorkspace();
    const [loaded, setLoaded] = useState<{ key: string; value?: unknown; error?: string } | null>(null);
    const richKind = richArtifactKind(artifact);
    const sourceKey = `${artifact.kind}:${url || ""}:${artifact.digest || ""}`;
    const fetched = loaded?.key === sourceKey ? loaded.value : null;
    const error = loaded?.key === sourceKey ? loaded.error : null;

    useEffect(() => {
        const controller = new AbortController();
        setLoaded(null);
        const richSource = safeHtmlArtifactSource(url, conversationId);
        if (richKind && typeof artifact.data !== "string" && !richSource) { setLoaded({ key: sourceKey, error: "This preview has no accessible conversation file." }); return () => controller.abort(); }
        if ((artifact.kind === "plotly" && !artifact.data && url) || (richKind && typeof artifact.data !== "string" && richSource)) {
            fetch(richKind ? richSource! : url!, { signal: controller.signal })
                .then(async (response) => {
                    if (!response.ok) throw new Error("The preview could not be loaded.");
                    return richKind ? readRichArtifact(response) : response.json();
                })
                .then((value) => { if (!controller.signal.aborted) setLoaded({ key: sourceKey, value }); })
                .catch((reason) => { if (!controller.signal.aborted) setLoaded({ key: sourceKey, error: reason instanceof Error ? reason.message : "The preview could not be loaded." }); });
        }
        return () => controller.abort();
    }, [artifact.kind, artifact.data, url, conversationId, richKind, sourceKey]);

    const frame = (content: ReactNode) => <div className="my-3 min-w-0 max-w-full">
        {!inCanvas && workspace && <div className="ui-artifact-header"><button type="button" className="ui-icon-button" title="Open in canvas" aria-label="Open in canvas"
            onClick={() => workspace.openArtifact(artifact, conversationId)}><Expand size={20} strokeWidth={1.8} /></button></div>}{content}
    </div>;
    if (richKind) {
        const source = typeof artifact.data === "string" ? artifact.data : typeof fetched === "string" ? fetched : null;
        if (source !== null) return frame(<RichSourcePreview key={sourceKey} source={source} kind={richKind} title={artifact.name} defaultView="preview"
            preview={richKind === "markdown" ? <MarkdownRenderer content={source} conversationId={conversationId} /> : undefined} />);
        return frame(<><p className="ui-surface-note" role="status">{error || "Loading preview…"}</p>
            {error && url && <a className="text-ui-sm text-foreground underline underline-offset-4" href={url} download={artifact.name}>Download {artifact.name}</a>}</>);
    }
    if (artifact.kind === "image") return frame(<figure><img src={url} alt="Figure" className="block h-auto max-w-full rounded-xl" /></figure>);
    if (artifact.kind === "plotly") {
        const figure = (artifact.data || fetched) as { data?: unknown[] } | null;
        if (figure && Array.isArray(figure.data)) return frame(<PlotRenderer figure={figure as { data?: Plotly.Data[] }} />);
        return error ? <p className="ui-surface-note" role="status">{error}</p> : null;
    }
    if (url && artifact.kind !== "json") return <a className="mt-2 flex w-fit max-w-full break-all text-ui-base text-foreground underline underline-offset-4" href={url} target="_blank" rel="noreferrer">{artifact.name}</a>;
    return null;
}
