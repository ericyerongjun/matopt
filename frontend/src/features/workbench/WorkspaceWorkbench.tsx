/** Document tabs adapted from ZCode packages/ui/src/app-shell/SidePaneTabTrigger.tsx,
 * upstream 29628c9acdb81b703bbd4080c207a0e7ce5e276e (Apache-2.0).
 * Geometry and middle-click close are migrated. Native tabs replace desktop DnD/RPC bindings. */
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, Download, FileText, FolderOpen, Trash2, X } from "lucide-react";
import type { WorkspaceFile } from "../../types/protocol.ts";
import { WorkspaceExplorer } from "./WorkspaceExplorer.tsx";
import { FilePreview } from "./FilePreview.tsx";
import { downloadUrl } from "./preview.ts";
import { WorkspaceFileIcon } from "./WorkspaceFileIcon.tsx";
import "./workbench.css";

export interface WorkspaceWorkbenchProps {
    conversationId: string | null;
    files: WorkspaceFile[];
    loading?: boolean;
    onRefresh: () => void | Promise<void>;
    onDelete?: (path: string) => void | Promise<void>;
    onUpload?: (files: File[]) => void | Promise<void>;
    initialPath?: string | null;
    emptyState?: ReactNode;
}
export function WorkspaceWorkbench(props: WorkspaceWorkbenchProps) {
    // Conversation changes dispose open documents and their in-flight HTTP streams immediately.
    return <WorkbenchSession key={props.conversationId ?? "new"} {...props} />;
}
function WorkbenchSession({ conversationId, files, loading = false, onRefresh, onDelete, onUpload, initialPath, emptyState }: WorkspaceWorkbenchProps) {
    const [openPaths, setOpenPaths] = useState<string[]>(initialPath ? [initialPath] : []);
    const [selectedPath, setSelectedPath] = useState<string | null>(initialPath ?? null);
    const [actionError, setActionError] = useState<string | null>(null);
    const [deleting, setDeleting] = useState(false);
    const [browsingFiles, setBrowsingFiles] = useState(false);
    const [compactFiles, setCompactFiles] = useState(() => window.matchMedia("(max-width: 620px), (max-width: 767px) and (max-height: 600px)").matches);
    const workbenchRef = useRef<HTMLDivElement>(null);
    const tabsRef = useRef<HTMLDivElement>(null);
    const documentId = useId();
    const handledInitialPath = useRef<string | null>(null);
    const selected = files.find((file) => file.path === selectedPath) ?? null;
    const openFiles = openPaths.map((path) => files.find((file) => file.path === path)).filter((file): file is WorkspaceFile => !!file);
    const openFile = (file: WorkspaceFile) => {
        setOpenPaths((paths) => paths.includes(file.path) ? paths : [...paths, file.path]);
        setSelectedPath(file.path); setActionError(null); setBrowsingFiles(false);
    };
    useEffect(() => {
        const element = workbenchRef.current;
        if (!element) return;
        // Match the container query, including a narrow dock on a wide desktop.
        // A hidden Workspace tab reports zero; retain its previous layout until visible.
        const update = (width: number) => { if (width > 0) setCompactFiles(width <= 620 || window.matchMedia("(max-width: 767px) and (max-height: 600px)").matches); };
        update(element.getBoundingClientRect().width);
        if (typeof ResizeObserver === "undefined") return;
        const observer = new ResizeObserver(([entry]) => { if (entry) update(entry.contentRect.width); });
        observer.observe(element);
        return () => observer.disconnect();
    }, []);
    useEffect(() => {
        if (!compactFiles) return;
        const target = browsingFiles || !selected
            ? workbenchRef.current?.querySelector<HTMLElement>('[role="treeitem"][aria-selected="true"]')
                ?? workbenchRef.current?.querySelector<HTMLElement>('[role="treeitem"][tabindex="0"]')
            : tabsRef.current?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]');
        target?.focus({ preventScroll: true });
    }, [browsingFiles, selected?.path, compactFiles]);
    useEffect(() => {
        if (initialPath && initialPath !== handledInitialPath.current) {
            const file = files.find((entry) => entry.path === initialPath);
            if (file) { openFile(file); handledInitialPath.current = initialPath; }
        }
        if (!initialPath) handledInitialPath.current = null;
    }, [initialPath, files]);
    useEffect(() => {
        if (loading) return;
        const available = new Set(files.map((file) => file.path));
        setOpenPaths((paths) => paths.every((path) => available.has(path)) ? paths : paths.filter((path) => available.has(path)));
        if (selectedPath && !available.has(selectedPath)) setSelectedPath(openPaths.find((path) => available.has(path)) ?? null);
    }, [files, loading, selectedPath, openPaths]);
    const closeFile = (path: string) => {
        setOpenPaths((paths) => paths.filter((item) => item !== path));
        if (path === selectedPath) {
            const index = openFiles.findIndex((file) => file.path === path);
            setSelectedPath(openFiles[index - 1]?.path ?? openFiles[index + 1]?.path ?? null);
        }
    };
    return <div ref={workbenchRef} className="workspace-workbench matopt-workbench" data-has-document={Boolean(selected)} data-browsing-files={browsingFiles}>
      <div className="workspace-workbench__body">
        {selected && <button type="button" className="workbench-return-document" aria-label={`Return to ${selected.name}`} title={selected.path} onClick={() => setBrowsingFiles(false)}><ArrowLeft size={17} aria-hidden="true" /><span>Back to {selected.name}</span></button>}
        <WorkspaceExplorer files={files} selectedPath={selectedPath} onSelect={openFile} loading={loading} onRefresh={onRefresh} onUpload={onUpload} />
        <div className="workspace-workbench__documents">
            <div className="workbench-document-bar">
                <button type="button" className="workbench-icon-button workbench-browse-files" aria-label="Browse workspace files" title="Browse workspace files" onClick={() => setBrowsingFiles(true)}><FolderOpen size={20} strokeWidth={1.8} aria-hidden="true" /><span>Files</span></button>
                {!openFiles.length && <span className="workbench-document-placeholder">Preview</span>}
                <div className="workbench-document-tabs" role="tablist" aria-label="Open workspace documents" ref={tabsRef}>
                    {openFiles.map((file, index) => <div className={`workbench-document-tab${selectedPath === file.path ? " is-active" : ""}`} key={file.path}
                        onAuxClick={(event) => { if (event.button === 1) { event.preventDefault(); event.stopPropagation(); closeFile(file.path); } }}>
                        <button type="button" role="tab" id={`${documentId}-tab-${index}`} aria-selected={selectedPath === file.path} aria-controls={`${documentId}-preview`}
                            tabIndex={selectedPath === file.path ? 0 : -1} title={file.path} onClick={(event) => {
                                // Preserve upstream behavior on browsers that dispatch click before auxclick.
                                if (event.button === 1) { event.preventDefault(); return; }
                                setSelectedPath(file.path); setActionError(null);
                            }}
                            onKeyDown={(event) => {
                                let nextIndex: number | null = null;
                                if (event.key === "ArrowRight") nextIndex = (index + 1) % openFiles.length;
                                if (event.key === "ArrowLeft") nextIndex = (index + openFiles.length - 1) % openFiles.length;
                                if (event.key === "Home") nextIndex = 0;
                                if (event.key === "End") nextIndex = openFiles.length - 1;
                                if (nextIndex !== null) {
                                    event.preventDefault(); setSelectedPath(openFiles[nextIndex].path);
                                    tabsRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[nextIndex]?.focus();
                                } else if (event.key === "Delete") { event.preventDefault(); closeFile(file.path); }
                            }}><WorkspaceFileIcon path={file.path} /><span>{file.name}</span></button>
                        <button type="button" className="workbench-document-tab__close" aria-label={`Close ${file.name}`} title={`Close ${file.name}`}
                            onClick={() => closeFile(file.path)}><X size={20} strokeWidth={1.8} /></button>
                    </div>)}
                </div>
                {selected && <div className="workbench-actions workbench-document-actions">
                    <a className="workbench-icon-button" href={downloadUrl(selected.url)} title={`Download ${selected.name}`} aria-label={`Download ${selected.name}`}><Download size={20} strokeWidth={1.8} /></a>
                    {onDelete && <button type="button" className="workbench-icon-button" title={`Delete ${selected.name}`} aria-label={`Delete ${selected.name}`} disabled={deleting}
                        onClick={() => {
                            setDeleting(true); setActionError(null);
                            void Promise.resolve().then(() => onDelete(selected.path)).then(() => closeFile(selected.path))
                                .catch((error) => setActionError(error instanceof Error ? error.message : "Could not delete file."))
                                .finally(() => setDeleting(false));
                        }}><Trash2 size={20} strokeWidth={1.8} /></button>}
                </div>}
            </div>
            {actionError && <p className="workbench-notice workbench-notice--error" role="alert">{actionError}</p>}
            <div id={`${documentId}-preview`} role={selected ? "tabpanel" : undefined} aria-labelledby={selected ? `${documentId}-tab-${openFiles.findIndex((file) => file.path === selected.path)}` : undefined} className="workspace-workbench__preview">
                {selected ? <FilePreview key={selected.path} file={selected} conversationId={conversationId} /> : emptyState ?? <div className="workbench-empty">
                    <span className="workbench-empty-icon"><FileText aria-hidden="true" /></span>
                    <h3>{files.length ? "Open a file" : "Start with your source files"}</h3>
                    <p>{files.length ? "Select a document, dataset, or image from the file list." : "Upload a file to keep your sources and generated work together."}</p>
                </div>}
            </div>
        </div>
      </div>
    </div>;
}
export { WorkspaceExplorer } from "./WorkspaceExplorer.tsx";
export { FilePreview } from "./FilePreview.tsx";
export default WorkspaceWorkbench;
