/** MatOpt preview canvas using the ZCode PreviewPane's bounded text-slice pattern.
 * Images, PDF, Markdown, data tables and Plotly retain MatOpt's existing renderers/file URLs. */
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { CodeViewer } from "../../zcode/components/ui/code-viewer.js";
import { RichSourcePreview } from "../../components/RichSourcePreview.tsx";
import type { WorkspaceFile } from "../../types/protocol.ts";
import { codeLanguage, downloadUrl, FILE_VIEWER_MAX_TEXT_BYTES, fileExtension, formatFileSize, latexMathPreview, parseDelimited, previewKind, readTextPreview, type TextSlice } from "./preview.ts";
import "./workbench.css";

const MarkdownRenderer = lazy(() => import("../../components/MarkdownRenderer.tsx"));
const PlotRenderer = lazy(() => import("../../components/PlotRenderer.tsx"));
export interface FilePreviewProps { file: WorkspaceFile; conversationId: string | null }

export function FilePreview({ file, conversationId }: FilePreviewProps) {
    const kind = previewKind(file);
    const [slice, setSlice] = useState<TextSlice | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [mode, setMode] = useState<"preview" | "source">(kind === "latex" || kind === "source" ? "source" : "preview");
    const [imageFailed, setImageFailed] = useState(false);
    useEffect(() => {
        const abort = new AbortController();
        setSlice(null); setError(null); setImageFailed(false);
        setMode(kind === "latex" || kind === "source" ? "source" : "preview");
        if (!["image", "pdf", "binary"].includes(kind)) {
            void readTextPreview(file.url, abort.signal).then((value) => { if (!abort.signal.aborted) setSlice(value); })
                .catch((failure) => { if (!abort.signal.aborted) setError(failure instanceof Error ? failure.message : "Could not load file."); });
        }
        return () => { abort.abort(); };
    }, [conversationId, file.path, file.url, file.size, file.mtime, kind]);
    const csv = useMemo(() => kind === "csv" && slice ? parseDelimited(slice.text, fileExtension(file.path) === "tsv" ? "\t" : ",") : null, [kind, slice, file.path]);
    const math = useMemo(() => kind === "latex" && slice ? latexMathPreview(slice.text) : "", [kind, slice]);
    let content;
    if (kind === "image") content = imageFailed ? <p className="workbench-notice" role="alert">Image could not be loaded. Download the original file to inspect it.</p>
        : <div className="file-preview__image"><img src={file.url} alt={file.name} onError={() => setImageFailed(true)} /></div>;
    else if (kind === "pdf") content = <PdfPreview key={`${conversationId}:${file.path}:${file.mtime}`} file={file} conversationId={conversationId} />;
    else if (kind === "binary") content = <div className="workbench-empty"><FileFormat name={file.name} /><p>Download this file to open it in its application.</p><a href={downloadUrl(file.url)}>Download {file.name}</a></div>;
    else if (error) content = <p className="workbench-notice workbench-notice--error" role="alert">{error}</p>;
    else if (!slice) content = <p className="workbench-notice" role="status">Loading file…</p>;
    else if (kind === "html") content = <RichSourcePreview source={slice.text} kind="html" title={file.name} defaultView="preview" complete={!slice.truncated} embedded />;
    else if (mode === "source") content = <CodeViewer className="file-preview__source" code={slice.text || "(empty file)"} language={codeLanguage(file.path)} showLineNumbers wrapLongLines enableSyntaxHighlighting fontSizePx={13} />;
    else if (kind === "markdown") content = <div className="file-preview__document"><MarkdownRenderer content={slice.text} conversationId={conversationId} /></div>;
    else if (kind === "latex") content = <div className="file-preview__document"><p className="workbench-notice">Formula preview. Open Code to view the complete LaTeX document.</p>
        {math ? <MarkdownRenderer content={math} conversationId={conversationId} /> : <p className="workbench-notice">No supported mathematical expressions found.</p>}</div>;
    else if (kind === "plotly") content = slice.truncated ? <p className="workbench-notice">This plot exceeds the preview limit. Download its complete JSON to open it.</p> : <PlotRenderer json={slice.text} />;
    else if (kind === "csv" && csv) content = csv.rows.length ? <div className="file-preview__table-wrap"><table className="file-preview__table">
        <thead><tr><th className="file-preview__row-number" aria-label="Row number">#</th>{Array.from({ length: Math.max(...csv.rows.map((row) => row.length)) }, (_, index) => <th key={index} scope="col">{csv.rows[0][index] || `Column ${index + 1}`}</th>)}</tr></thead>
        <tbody>{csv.rows.slice(1).map((row, index) => <tr key={index}><th scope="row" className="file-preview__row-number">{index + 1}</th>
            {Array.from({ length: Math.max(csv.rows[0].length, row.length) }, (_, column) => <td key={column}>{row[column] ?? ""}</td>)}</tr>)}</tbody>
    </table>{(csv.truncated || csv.columnsTruncated) && <p className="workbench-notice">Table preview shows up to 200 data rows and 40 columns. Download the file for the full table.</p>}</div> : <p className="workbench-notice">Empty table.</p>;
    else content = <CodeViewer className="file-preview__source" code={slice.text} language={codeLanguage(file.path)} showLineNumbers wrapLongLines fontSizePx={13} />;

    const switchable = ["markdown", "latex", "csv", "plotly"].includes(kind);
    return <section className="file-preview matopt-file-preview" aria-label={`Preview ${file.path}`}>
        <div className="file-preview__toolbar">
            <span className="file-preview__breadcrumb" title={file.path}>{file.path.split("/").map((segment, index) => <span key={index}>{index > 0 && <span className="file-preview__separator">/</span>}{segment}</span>)}</span>
            <div className="file-preview__modes" role={switchable ? "group" : undefined} aria-label={switchable ? "File view" : undefined}>{switchable && <>
                <button type="button" aria-pressed={mode === "preview"} onClick={() => setMode("preview")}>Preview</button>
                <button type="button" aria-pressed={mode === "source"} onClick={() => setMode("source")}>Code</button>
            </>}</div>
        </div>
        <Suspense fallback={<p className="workbench-notice" role="status">Loading preview…</p>}><div className="file-preview__content">{content}</div></Suspense>
        <div className="file-preview__footer"><span>{kind === "latex" ? "LaTeX" : kind === "source" ? codeLanguage(file.path) : kind} · {formatFileSize(file.size)}</span>
            {slice?.truncated && <span>Preview limited to {formatFileSize(FILE_VIEWER_MAX_TEXT_BYTES)} · <a href={downloadUrl(file.url)}>Download full file</a></span>}
        </div>
    </section>;
}

/** Render one bounded page as pixels, independent of browser PDF plugins. */
function PdfPreview({ file, conversationId }: FilePreviewProps) {
    const [page, setPage] = useState(1);
    const [total, setTotal] = useState<number | null>(null);
    const [image, setImage] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    useEffect(() => {
        const abort = new AbortController();
        let objectUrl: string | undefined;
        setImage(null); setError(null);
        if (!conversationId) { setError("Open a conversation to preview this PDF."); return; }
        const path = file.path.split("/").map(encodeURIComponent).join("/");
        void fetch(`/api/files/${encodeURIComponent(conversationId)}/pdf-preview/${path}?page=${page}`, { signal: abort.signal })
            .then(async (response) => {
                if (!response.ok) {
                    const detail = await response.json().catch(() => null);
                    throw new Error(typeof detail?.detail === "string" ? detail.detail : "Could not render this PDF. Download the original to inspect it.");
                }
                const blob = await response.blob();
                if (abort.signal.aborted) return;
                const pages = Number(response.headers.get("X-PDF-Pages"));
                setTotal(Number.isInteger(pages) && pages > 0 ? pages : 1);
                objectUrl = URL.createObjectURL(blob); setImage(objectUrl);
            }).catch((failure) => {
                if (!abort.signal.aborted) setError(failure instanceof Error ? failure.message : "Could not render this PDF.");
            });
        return () => { abort.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
    }, [conversationId, file.path, page]);
    return <div className="file-preview__pdf">
        <nav className="file-preview__pdf-pages" aria-label="PDF pages">
            <button type="button" aria-label="Previous page" title="Previous page" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}><ChevronLeft size={20} strokeWidth={1.8} /></button>
            <span role="status">Page {page}{total !== null ? ` of ${total}` : ""}</span>
            <button type="button" aria-label="Next page" title="Next page" disabled={total === null || page >= total} onClick={() => setPage((value) => value + 1)}><ChevronRight size={20} strokeWidth={1.8} /></button>
        </nav>
        {error ? <p className="workbench-notice" role="alert">{error}</p> : image
            ? <img src={image} alt={`${file.name}, page ${page}${total !== null ? ` of ${total}` : ""}`} />
            : <p className="workbench-notice" role="status">Rendering PDF page…</p>}
    </div>;
}
function FileFormat({ name }: { name: string }) { return <strong>{name}</strong>; }
export default FilePreview;
