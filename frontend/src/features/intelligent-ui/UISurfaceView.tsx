import { Component, type ErrorInfo, type ReactNode } from "react";
import { ArrowUpRight, Download, Expand, Loader2, RotateCcw, Square } from "lucide-react";
import type { UISurface } from "../../types/ui.ts";
import MarkdownRenderer from "../../components/MarkdownRenderer.tsx";
import { ArtifactView } from "../../components/ArtifactView.tsx";
import { hasChangedActionInputs, isSurface, surfaceExport } from "./model.ts";
import { MiniAppFrame } from "./MiniAppFrame.tsx";
import { hasActionButton, NativeSurface } from "./NativeSurface.tsx";
import { UiWorkspaceProvider, useOptionalUiWorkspace, useSurface, useUiWorkspace } from "./UiWorkspace.tsx";
import "./intelligent-ui.css";

class SurfaceBoundary extends Component<{ children: ReactNode; fallback: string; revision: number }, { failed: boolean }> {
    state = { failed: false };
    static getDerivedStateFromError() { return { failed: true }; }
    componentDidCatch(_error: Error, _info: ErrorInfo) { /* The user-visible text fallback is the recovery path. */ }
    componentDidUpdate(previous: Readonly<{ children: ReactNode; fallback: string; revision: number }>) {
        if (previous.revision !== this.props.revision && this.state.failed) this.setState({ failed: false });
    }
    render() { return this.state.failed ? <div role="status"><p className="ui-surface-note">This view could not be displayed.</p><MarkdownRenderer content={this.props.fallback} /></div> : this.props.children; }
}

export function UISurfaceView({ surface, inCanvas = false }: { surface: UISurface; inCanvas?: boolean }) {
    const workspace = useOptionalUiWorkspace();
    const fallback = typeof surface?.fallback === "string" ? surface.fallback : "This interactive result uses an unsupported format.";
    if (!isSurface(surface)) return <div className="ui-surface ui-surface-fallback"><MarkdownRenderer content={fallback} /></div>;
    if (!workspace) return <UiWorkspaceProvider conversationId={null}><SurfaceContent surface={surface} inCanvas={inCanvas} /></UiWorkspaceProvider>;
    return <SurfaceContent surface={surface} inCanvas={inCanvas} />;
}

function SurfaceContent({ surface: incoming, inCanvas }: { surface: UISurface; inCanvas: boolean }) {
    const { workspace, record } = useSurface(incoming);
    const { surface, state, busy, saving, error } = record;
    const committed = surface.status === "committed";
    const needsComputation = hasChangedActionInputs(surface, state, record.resultState);
    const download = () => {
        const blob = new Blob([surfaceExport(surface, state, record.resultState)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a"); link.href = url; link.download = `${surface.title.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").slice(0, 80) || "interactive-result"}.json`;
        link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    if (surface.status === "error" || !(surface.kind === "native" ? workspace.capabilities?.native : workspace.capabilities?.mini_apps)) {
        return <section className="ui-surface ui-surface-fallback" aria-label={surface.title} data-ui-surface={surface.id}>
            <header className="ui-surface-header"><h3>{surface.title}</h3></header>
            <MarkdownRenderer content={surface.fallback} conversationId={workspace.conversationId} />
            <p className="ui-surface-note" role="status">{surface.status === "error" ? "The interactive view could not be completed. The result remains available above." : !workspace.capabilitiesLoaded ? "Checking interactive view…" : workspace.capabilities ? "Interactive views are disabled. The result remains available above." : "The interactive view is unavailable. The result remains available above."}</p>
        </section>;
    }
    return <section className={`ui-surface ${inCanvas ? "ui-surface-canvas" : ""}`} aria-label={surface.title} aria-busy={busy || !committed}
        data-ui-surface={surface.id} data-ui-revision={surface.revision} data-ui-status={surface.status}>
        <header className="ui-surface-header">
            <h3>{surface.title}</h3>
            <div className="ui-surface-header-actions">
                {surface.status === "preview" && <span className="ui-surface-note" role="status">Building…</span>}
                {!inCanvas && <button type="button" className="ui-icon-button" aria-label={`Open ${surface.title} in canvas`} title="Open in canvas" onClick={() => workspace.openSurface(surface)}><Expand size={20} strokeWidth={1.8} /></button>}
            </div>
        </header>
        <SurfaceBoundary fallback={surface.fallback} revision={surface.revision}>
            {surface.kind === "native" ? <NativeSurface record={record} /> : committed && surface.app?.html
                ? <MiniAppFrame surfaceId={surface.id} title={surface.title} html={surface.app.html} state={state} onStateChange={(next) => workspace.updateState(surface.id, next)} fallback={surface.fallback} />
                : <div className="ui-surface-note" role="status">{surface.status === "preview" ? "Preparing preview…" : surface.fallback}</div>}
        </SurfaceBoundary>
        {surface.actions.some((action) => !hasActionButton(surface, action.id)) && <div className="ui-surface-actions">
            {surface.actions.filter((action) => !hasActionButton(surface, action.id)).map((action) => <button type="button" key={action.id} className="ui-action-button" disabled={!committed || busy || workspace.conversationBusy || !workspace.conversationId}
                onClick={() => workspace.runAction(surface.id, action.id)}>{action.label}</button>)}
        </div>}
        {needsComputation && !busy && <p className="ui-surface-note ui-recompute-note" role="status">Parameters changed. Recompute to update this result.</p>}
        {busy && <div className="ui-surface-progress" role="status"><Loader2 className="ui-spin" size={20} strokeWidth={1.8} /><span>Computing with your parameters…</span>
            <button type="button" className="ui-text-button" onClick={() => workspace.stopAction(surface.id)}><Square size={20} strokeWidth={1.8} />Stop</button></div>}
        {error && <div className="ui-surface-error" role="alert"><p>{error}</p><button type="button" className="ui-text-button" onClick={() => workspace.reloadSurface(surface.id)} disabled={busy}>Reload saved result</button></div>}
        <footer className="ui-surface-footer">
            <button type="button" className="ui-text-button" disabled={!committed || busy || JSON.stringify(state) === JSON.stringify(record.initialState)} onClick={() => workspace.resetState(surface.id)}><RotateCcw size={20} strokeWidth={1.8} />Reset</button>
            {workspace.canExplain && <button type="button" className="ui-text-button" disabled={!committed || busy} onClick={() => workspace.explainSurface(surface.id)}><ArrowUpRight size={20} strokeWidth={1.8} />Explain this result</button>}
            <button type="button" className="ui-text-button" onClick={download} disabled={!committed}><Download size={20} strokeWidth={1.8} />Export</button>
            <span className="ui-save-status" role="status">{saving ? "Saving…" : record.editVersion > record.savedVersion && workspace.conversationId ? workspace.conversationBusy ? "Saves when the current task finishes" : "Unsaved parameters" : ""}</span>
        </footer>
        {surface.fallback && <details className="ui-result-description"><summary>About this result</summary><MarkdownRenderer content={surface.fallback} conversationId={workspace.conversationId} /></details>}
    </section>;
}

export function UiCanvas({ className = "" }: { className?: string }) {
    const { selection, records } = useUiWorkspace();
    if (!selection) return <div className={`ui-canvas-empty ${className}`}><p>Open a result from the conversation to view it here.</p></div>;
    if (selection.kind === "artifact") return <div className={`ui-canvas-content ${className}`}><h2 className="ui-canvas-title">{selection.artifact.name}</h2><ArtifactView artifact={selection.artifact} conversationId={selection.conversationId} inCanvas /></div>;
    const record = records[selection.surfaceId];
    return <div className={`ui-canvas-content ${className}`}>{record ? <UISurfaceView surface={record.surface} inCanvas /> : <p className="ui-surface-note">This result is no longer available in the current conversation.</p>}</div>;
}
