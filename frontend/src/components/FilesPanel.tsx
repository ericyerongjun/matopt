/**
 * Right-side workspace file browser for the active conversation.
 */

import React, { useMemo, useState } from "react";
import { Download, FileText, Folder, Trash2, X } from "lucide-react";
import MarkdownRenderer from "./MarkdownRenderer";
import PlotRenderer from "./PlotRenderer";
import type { WorkspaceFile } from "../types/protocol";
import "./FilesPanel.css";

interface Props {
    conversationId: string;
    files: WorkspaceFile[];
    onDelete: (path: string) => void;
    onUpload: (files: FileList | File[]) => void;
    onClose: () => void;
}

function groupByFolder(files: WorkspaceFile[]) {
    const groups = new Map<string, WorkspaceFile[]>();
    for (const file of files) {
        const slash = file.path.lastIndexOf("/");
        const folder = slash === -1 ? "" : file.path.slice(0, slash);
        const list = groups.get(folder) ?? [];
        list.push(file);
        groups.set(folder, list);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
}

export default function FilesPanel({ conversationId, files, onDelete, onUpload, onClose }: Props) {
    const [preview, setPreview] = useState<WorkspaceFile | null>(null);
    const groups = useMemo(() => groupByFolder(files), [files]);

    return (
        <aside className="files-panel">
            <div className="files-panel__head">
                <strong>Workspace</strong>
                <button type="button" className="icon-btn" onClick={onClose} title="Close files">
                    <X size={16} />
                </button>
            </div>
            <label className="files-panel__upload">
                Upload
                <input
                    type="file"
                    multiple
                    hidden
                    onChange={(e) => {
                        if (e.target.files?.length) onUpload(e.target.files);
                        e.target.value = "";
                    }}
                />
            </label>
            <div className="files-panel__list">
                {groups.length === 0 && <div className="files-panel__empty">No files yet</div>}
                {groups.map(([folder, items]) => (
                    <div key={folder || "root"} className="files-panel__group">
                        <div className="files-panel__folder">
                            <Folder size={12} />
                            {folder || "/"}
                        </div>
                        {items.map((file) => (
                            <div key={file.path} className="files-panel__item">
                                <button type="button" className="files-panel__name" onClick={() => setPreview(file)}>
                                    <FileText size={14} />
                                    {file.name}
                                </button>
                                <a className="icon-btn" href={`${file.url}?download=1`} title="Download">
                                    <Download size={14} />
                                </a>
                                <button type="button" className="icon-btn" title="Delete" onClick={() => onDelete(file.path)}>
                                    <Trash2 size={14} />
                                </button>
                            </div>
                        ))}
                    </div>
                ))}
            </div>
            {preview && (
                <div className="files-preview">
                    <div className="files-preview__bar">
                        <span>{preview.path}</span>
                        <button type="button" className="icon-btn" onClick={() => setPreview(null)}>
                            <X size={14} />
                        </button>
                    </div>
                    <PreviewBody file={preview} conversationId={conversationId} />
                </div>
            )}
        </aside>
    );
}

function PreviewBody({ file, conversationId }: { file: WorkspaceFile; conversationId: string }) {
    const [text, setText] = useState<string | null>(null);
    React.useEffect(() => {
        if (file.kind === "image" || file.kind === "plotly") return;
        fetch(file.url)
            .then((r) => r.text())
            .then(setText)
            .catch(() => setText(null));
    }, [file.url, file.kind]);

    if (file.kind === "image") {
        return <img className="files-preview__img" src={file.url} alt={file.name} />;
    }
    if (file.kind === "plotly") {
        return <PlotlyFromUrl url={file.url} />;
    }
    if (file.kind === "markdown" || file.name.endsWith(".md")) {
        return <MarkdownRenderer content={text || ""} conversationId={conversationId} />;
    }
    return <pre className="files-preview__pre">{text ?? "Loading…"}</pre>;
}

function PlotlyFromUrl({ url }: { url: string }) {
    const [json, setJson] = useState<string | null>(null);
    React.useEffect(() => {
        fetch(url)
            .then((r) => r.text())
            .then(setJson)
            .catch(() => setJson(null));
    }, [url]);
    if (!json) return <div className="files-panel__empty">Loading plot…</div>;
    return <PlotRenderer json={json} />;
}
