/** MatOpt HTTP adapter for ZCode's WorkspaceFileTreeRowView / WorkspaceFileTreeList.
 * Row layout, directory compaction, hierarchy guides and collapse semantics come from
 * ZCode 29628c9acdb81b703bbd4080c207a0e7ce5e276e (Apache-2.0).
 * Desktop RPC, Git status, file mutations and drag/drop are not included. */
import { useMemo, useRef, useState, type KeyboardEvent } from "react";
import { ChevronRight, ChevronsDownUp, RefreshCw, Search, Upload } from "lucide-react";
import type { WorkspaceFile } from "../../types/protocol.ts";
import { buildWorkspaceTree, flattenWorkspaceFileTreeRows, replaceSetValue } from "./tree.ts";
import { getWorkspaceFileTreeHierarchyGuideStyle } from "./hierarchyGuides.ts";
import { WorkspaceFileTreeRowName } from "./WorkspaceFileTreeRowName.tsx";
import { WorkspaceFileIcon } from "./WorkspaceFileIcon.tsx";
import "./workbench.css";

export interface WorkspaceExplorerProps {
    files: WorkspaceFile[];
    selectedPath?: string | null;
    onSelect: (file: WorkspaceFile) => void;
    loading?: boolean;
    onRefresh?: () => void | Promise<void>;
    onUpload?: (files: File[]) => void | Promise<void>;
}
export function WorkspaceExplorer({ files, selectedPath, onSelect, loading = false, onRefresh, onUpload }: WorkspaceExplorerProps) {
    const tree = useMemo(() => buildWorkspaceTree(files), [files]);
    const fileByPath = useMemo(() => new Map(files.map((file) => [file.path, file])), [files]);
    const [expanded, setExpanded] = useState<Set<string> | null>(null);
    const [query, setQuery] = useState("");
    const [visibleLimit, setVisibleLimit] = useState(200);
    const [focusedPath, setFocusedPath] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);
    const [uploading, setUploading] = useState(false);
    const treeRef = useRef<HTMLDivElement>(null);
    const uploadRef = useRef<HTMLInputElement>(null);
    const expandedPaths = expanded ?? tree.directoryPaths;
    const rows = useMemo(() => {
        const all = flattenWorkspaceFileTreeRows({ rootPath: "", childrenByDirectory: tree.childrenByDirectory,
            expandedPaths: query ? tree.directoryPaths : expandedPaths, loadedDirectoryPaths: tree.directoryPaths,
            loadingDirectoryPaths: new Set(), errorByDirectory: new Map(), flattenEmptyDirectories: true });
        const search = query.trim().toLocaleLowerCase();
        if (!search) return all;
        const matching = all.filter((row) => row.path.toLocaleLowerCase().includes(search));
        return all.filter((row) => matching.includes(row) || row.type === "directory" && matching.some((match) => match.path.startsWith(`${row.path}/`)));
    }, [tree, expandedPaths, query]);
    const shownRows = rows.slice(0, visibleLimit);
    const toggleDirectory = (path: string, compactedPaths: string[] | undefined, currentlyExpanded: boolean) => {
        let next = new Set(expandedPaths);
        for (const part of compactedPaths ?? [path]) next = replaceSetValue(next, part, !currentlyExpanded);
        setExpanded(next);
    };
    const focusRow = (index: number) => {
        const row = shownRows[Math.max(0, Math.min(shownRows.length - 1, index))];
        if (!row) return;
        setFocusedPath(row.path);
        treeRef.current?.querySelectorAll<HTMLButtonElement>('[role="treeitem"]')[Math.max(0, Math.min(shownRows.length - 1, index))]?.focus();
    };
    const keyDown = (event: KeyboardEvent, index: number) => {
        const row = shownRows[index];
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault(); focusRow(index + (event.key === "ArrowDown" ? 1 : -1));
        } else if (event.key === "Home" || event.key === "End") {
            event.preventDefault(); focusRow(event.key === "Home" ? 0 : shownRows.length - 1);
        } else if (event.key === "ArrowRight" && row.type === "directory") {
            event.preventDefault();
            if (!row.expanded) toggleDirectory(row.path, row.compactedPaths, false); else focusRow(index + 1);
        } else if (event.key === "ArrowLeft") {
            event.preventDefault();
            if (row.type === "directory" && row.expanded) toggleDirectory(row.path, row.compactedPaths, true);
            else for (let parent = index - 1; parent >= 0; parent--) if (shownRows[parent].depth < row.depth) { focusRow(parent); break; }
        }
    };
    const runAction = async (action: () => void | Promise<void>) => {
        setActionError(null);
        try { await action(); } catch (error) { setActionError(error instanceof Error ? error.message : "Workspace action failed."); }
    };
    return <aside className="workspace-explorer matopt-file-explorer" aria-label="Workspace files">
        <div className="workspace-explorer__header">
            <span>Files <span className="workspace-explorer__count">{files.length}</span></span>
            <div className="workbench-actions">
                {onUpload && <button type="button" className="workbench-icon-button" title="Upload files" aria-label="Upload files" disabled={uploading}
                    onClick={() => uploadRef.current?.click()}><Upload size={20} strokeWidth={1.8} /></button>}
                <button type="button" className="workbench-icon-button" title="Collapse folders" aria-label="Collapse folders"
                    onClick={() => { setExpanded(new Set()); setQuery(""); }}><ChevronsDownUp size={20} strokeWidth={1.8} /></button>
                {onRefresh && <button type="button" className="workbench-icon-button" title="Refresh files" aria-label="Refresh files" disabled={loading}
                    onClick={() => void runAction(onRefresh)}><RefreshCw size={20} strokeWidth={1.8} className={loading ? "workbench-spinning" : undefined} /></button>}
            </div>
        </div>
        {onUpload && <input type="file" multiple hidden ref={uploadRef} onChange={(event) => {
            const chosen = Array.from(event.currentTarget.files ?? []);
            event.currentTarget.value = "";
            if (!chosen.length) return;
            setUploading(true);
            void runAction(() => onUpload(chosen)).finally(() => setUploading(false));
        }} />}
        <label className="workspace-explorer__search"><Search size={20} strokeWidth={1.8} aria-hidden="true" /><input aria-label="Filter files" placeholder="Search files" value={query}
            onChange={(event) => { setQuery(event.target.value); setVisibleLimit(200); }} /></label>
        {actionError && <p className="workbench-notice workbench-notice--error" role="alert">{actionError}</p>}
        <div className="workspace-explorer__tree" role="tree" aria-label="Workspace file tree" aria-busy={loading} ref={treeRef}>
            {shownRows.map((row, index) => {
                const file = fileByPath.get(row.path);
                const guideStyle = getWorkspaceFileTreeHierarchyGuideStyle(row.depth);
                return <button type="button" key={row.path} role="treeitem" aria-level={row.depth + 1}
                    aria-expanded={row.type === "directory" ? row.expanded : undefined}
                    aria-selected={row.type === "file" ? selectedPath === row.path : undefined}
                    tabIndex={focusedPath === row.path || !shownRows.some((item) => item.path === focusedPath) && index === 0 ? 0 : -1}
                    className={`workspace-tree-row${selectedPath === row.path ? " workspace-tree-row--selected" : ""}`}
                    style={{ paddingLeft: `calc(${row.depth} * 0.75rem + 0.5rem)` }} title={row.path}
                    onFocus={() => setFocusedPath(row.path)} onKeyDown={(event) => keyDown(event, index)}
                    onClick={() => { if (row.type === "directory") toggleDirectory(row.path, row.compactedPaths, row.expanded); else if (file) onSelect(file); }}>
                    {guideStyle && <span className="workspace-tree-row__guides" style={guideStyle} aria-hidden="true" />}
                    {row.type === "directory" ? <span className="workspace-tree-row__chevron-slot"><ChevronRight size={20} strokeWidth={1.8} className={`workspace-tree-row__chevron${row.expanded ? " is-expanded" : ""}`} aria-hidden="true" /></span>
                        : <WorkspaceFileIcon path={row.path} className="workspace-tree-row__icon" />}
                    <WorkspaceFileTreeRowName name={row.name} className="workspace-tree-row__name" slashClassName="workspace-tree-row__slash" />
                </button>;
            })}
            {!rows.length && <div className="workspace-explorer__empty">{loading ? "Loading files…" : query ? "No matching files" : "No workspace files yet"}</div>}
            {rows.length > visibleLimit && <button type="button" className="workbench-more" onClick={() => setVisibleLimit((limit) => limit + 200)}>
                Show more ({rows.length - visibleLimit})</button>}
        </div>
    </aside>;
}
export default WorkspaceExplorer;
