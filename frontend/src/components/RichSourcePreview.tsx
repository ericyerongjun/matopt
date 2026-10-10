import { useId, useMemo, useState, type ReactNode } from "react";
import { Check, Code2, Copy, Eye } from "lucide-react";
import { MiniAppFrame } from "../features/intelligent-ui/MiniAppFrame.tsx";
import { useOptionalUiWorkspace } from "../features/intelligent-ui/UiWorkspace.tsx";
import { richSourcePreviewIssue, svgImageDataUrl, type RichSourceKind } from "../utils/richSource.ts";
import css from "./RichSourcePreview.module.css";

export function RichSourcePreview({ source, kind, title, defaultView = "code", complete = true, embedded = false, preview }: {
    source: string; kind: RichSourceKind; title?: string; defaultView?: "code" | "preview";
    complete?: boolean; embedded?: boolean; preview?: ReactNode;
}) {
    const id = useId();
    const workspace = useOptionalUiWorkspace();
    const [selection, setSelection] = useState({ source, view: defaultView });
    const [copiedSource, setCopiedSource] = useState<string | null>(null);
    const [copyError, setCopyError] = useState(false);
    const [failedImage, setFailedImage] = useState<string | null>(null);
    const syntaxIssue = useMemo(() => richSourcePreviewIssue(source, kind), [source, kind]);
    const imageUrl = useMemo(() => kind === "svg" && !syntaxIssue ? svgImageDataUrl(source) : undefined, [source, kind, syntaxIssue]);
    const issue = !complete ? "Preview is available once this output is complete." : syntaxIssue
        ?? (failedImage === source ? "This image could not be previewed. Its source is still available." : null)
        ?? (kind === "html" && workspace && !workspace.capabilities?.mini_apps
            ? !workspace.capabilitiesLoaded ? "Checking preview availability…" : "Interactive previews are unavailable. The source is available below."
            : null);
    const requestedView = selection.source === source ? selection.view : defaultView;
    const view = issue ? "code" : requestedView;
    const label = kind === "markdown" ? "Markdown" : kind.toUpperCase();
    const iconSize = embedded ? 18 : 20;
    const copied = copiedSource === source;
    const copy = async () => {
        try { await navigator.clipboard.writeText(source); setCopiedSource(source); setCopyError(false); }
        catch { setCopyError(true); }
    };

    return <section className={`${css.root} ${embedded ? css.embedded : ""}`} data-rich-source={kind} data-rich-view={view} aria-label={title || `${label} output`}>
        <div className={css.toolbar}>
            <div className={css.views} role="group" aria-label={`${label} view`}>
                <button type="button" aria-pressed={view === "preview"} disabled={Boolean(issue)} aria-describedby={issue ? `${id}-notice` : undefined}
                    onClick={() => setSelection({ source, view: "preview" })}><Eye size={iconSize} strokeWidth={1.8} aria-hidden="true" />Preview</button>
                <button type="button" aria-pressed={view === "code"} onClick={() => setSelection({ source, view: "code" })}><Code2 size={iconSize} strokeWidth={1.8} aria-hidden="true" />Code</button>
            </div>
            {!embedded && <button className={css.copy} type="button" aria-label={copied ? "Copied source" : "Copy source"} title={copied ? "Copied" : "Copy source"} onClick={copy}>
                {copied ? <Check size={iconSize} strokeWidth={1.8} /> : <Copy size={iconSize} strokeWidth={1.8} />}
            </button>}
        </div>
        {issue && <p id={`${id}-notice`} className={css.notice} role="status">{issue}</p>}
        {copyError && <p className={css.notice} role="status">Copy was unavailable. You can select the source below.</p>}
        {view === "code" ? <pre className={css.source} tabIndex={0} aria-label={`${label} source`}><code>{source}</code></pre>
            : kind === "markdown" ? <div className={css.markdown}>{preview}</div>
                : kind === "svg" ? <img className={css.image} src={imageUrl} alt={title || "SVG preview"} onError={() => setFailedImage(source)} />
                : <MiniAppFrame surfaceId={`rich:${id}`} title={title || `${label} preview`} html={source} fallback="This preview is unavailable. Switch to Code to read the source." />}
    </section>;
}
