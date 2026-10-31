/** Files a turn added, modified or deleted, with per-file diffs. */
import { Component, lazy, Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import { ChevronRight, FileDiff, RefreshCw } from "lucide-react";
import { getTurnDiff, splitUnifiedDiff, type TurnDiff } from "../../services/changes.ts";
import { errorMessage, isUnavailable } from "../../services/api.ts";
import type { ChangedFile } from "../../types/protocol.ts";
import { PlainDiff } from "./PlainDiff.tsx";
import "./changes.css";

const HighlightedDiff = lazy(() => import("./HighlightedDiff.tsx"));
const STATUS: Record<ChangedFile["status"], string> = { A: "Added", M: "Modified", D: "Deleted" };

/** Highlighting is an enhancement; any renderer failure falls back to plain text. */
class DiffBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
    state = { failed: false };
    static getDerivedStateFromError() { return { failed: true }; }
    render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

export function changeTotals(files: ChangedFile[]) {
    return files.reduce((sum, file) => ({ added: sum.added + (file.added ?? 0), removed: sum.removed + (file.removed ?? 0) }), { added: 0, removed: 0 });
}

export function ChangeCounts({ file }: { file: Pick<ChangedFile, "added" | "removed"> }) {
    if (file.added === undefined && file.removed === undefined) return null;
    return <span className="matopt-change-counts">
        {file.added !== undefined && <span className="is-add" aria-label={`${file.added} lines added`}>+{file.added}</span>}
        {file.removed !== undefined && <span className="is-del" aria-label={`${file.removed} lines removed`}>−{file.removed}</span>}
    </span>;
}

function FileChange({ file, patch, highlight }: { file: ChangedFile; patch?: string; highlight: boolean }) {
    const [open, setOpen] = useState(false);
    const notice = file.binary ? "Binary file. No text diff is shown."
        : !patch ? file.untracked ? "New untracked file. Its contents are not part of the diff." : "No text diff is available for this file."
        : null;
    return <li className="matopt-change-file" data-status={file.status}>
        <details open={open} onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}>
            <summary>
                <ChevronRight className="matopt-change-chevron" aria-hidden="true" />
                <span className="matopt-change-status" data-status={file.status} title={STATUS[file.status]}><span aria-hidden="true">{file.status}</span><span className="sr-only">{STATUS[file.status]}</span></span>
                <span className="matopt-change-path" title={file.path}>{file.path}</span>
                {file.untracked && <span className="matopt-change-flag">Untracked</span>}
                {file.binary && <span className="matopt-change-flag">Binary</span>}
                <ChangeCounts file={file} />
            </summary>
            {open && (notice ? <p className="matopt-change-notice">{notice}</p>
                : highlight ? <DiffBoundary fallback={<PlainDiff patch={patch!} path={file.path} />}><Suspense fallback={<PlainDiff patch={patch!} path={file.path} />}><div className="matopt-diff-highlighted" aria-label={`Changes to ${file.path}`} role="region"><HighlightedDiff patch={patch!} /></div></Suspense></DiffBoundary>
                : <PlainDiff patch={patch!} path={file.path} />)}
        </details>
    </li>;
}

export function ChangesView({ conversationId, turn, turns = [], onTurnChange, highlight = true, revision = 0 }: {
    conversationId: string | null; turn: number | null; turns?: number[]; onTurnChange?: (turn: number) => void;
    /** Plain text only (tests and very large diffs). */
    highlight?: boolean; revision?: number;
}) {
    const [state, setState] = useState<{ diff: TurnDiff | null; loading: boolean; error: string | null; unavailable: boolean }>({ diff: null, loading: false, error: null, unavailable: false });
    const [reload, setReload] = useState(0);
    useEffect(() => {
        if (!conversationId || turn === null) { setState({ diff: null, loading: false, error: null, unavailable: false }); return; }
        const controller = new AbortController();
        setState((previous) => ({ ...previous, loading: true, error: null }));
        getTurnDiff(conversationId, turn, controller.signal).then(
            (diff) => { if (!controller.signal.aborted) setState({ diff, loading: false, error: null, unavailable: false }); },
            (error) => {
                if (controller.signal.aborted) return;
                setState({ diff: null, loading: false, unavailable: isUnavailable(error), error: isUnavailable(error) ? null : errorMessage(error, "Could not load the changes.") });
            });
        return () => controller.abort();
    }, [conversationId, turn, reload, revision]);
    const patches = useMemo(() => splitUnifiedDiff(state.diff?.diff ?? ""), [state.diff]);
    const files = state.diff?.files ?? [];
    const totals = changeTotals(files);
    const choices = [...new Set([...(turn !== null ? [turn] : []), ...turns])].sort((a, b) => b - a);

    if (turn === null) return <div className="matopt-changes-empty"><span className="matopt-workspace-empty-icon"><FileDiff aria-hidden="true" /></span><h3>No changes selected</h3><p>When a turn adds, edits or removes files, choose “files changed” under the answer to review them here.</p></div>;
    return <div className="matopt-changes">
        <div className="matopt-changes-bar">
            {choices.length > 1 && onTurnChange ? <label className="matopt-changes-turn"><span>Turn</span>
                <select value={turn} onChange={(event) => onTurnChange(Number(event.target.value))}>{choices.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
                : <span className="matopt-changes-turn">Turn {turn}</span>}
            {files.length > 0 && <span className="matopt-changes-summary">{files.length} {files.length === 1 ? "file" : "files"} changed<ChangeCounts file={totals} /></span>}
            <button type="button" className="matopt-chrome-button" aria-label="Reload changes" title="Reload changes" disabled={state.loading} onClick={() => setReload((value) => value + 1)}><RefreshCw aria-hidden="true" /></button>
        </div>
        {state.loading && !state.diff && <p className="matopt-changes-note" role="status">Loading changes…</p>}
        {state.unavailable && <p className="matopt-changes-note" role="status">Change review is not available on this server.</p>}
        {state.error && <p className="matopt-changes-note matopt-changes-error" role="alert">{state.error}</p>}
        {state.diff?.truncated && <p className="matopt-changes-note" role="note">This diff was truncated because it is very large. Some changes are not shown.</p>}
        {state.diff && !files.length && <p className="matopt-changes-note">This turn did not change any files.</p>}
        {files.length > 0 && <ul className="matopt-change-files" aria-label={`Files changed in turn ${turn}`}>
            {files.map((file) => <FileChange key={file.path} file={file} patch={patches.get(file.path)} highlight={highlight} />)}
        </ul>}
    </div>;
}
