import { useId, type ReactNode } from "react";
import { ArrowLeft, FileDiff, FileText, FolderOpen, Layers, X } from "lucide-react";
import { useUiWorkspace } from "../intelligent-ui/UiWorkspace.tsx";
import { UiCanvas } from "../intelligent-ui/UISurfaceView.tsx";
import type { Artifact } from "../../types/protocol.ts";
import type { UISurface } from "../../types/ui.ts";
import "./workspace-panel.css";

export type WorkspaceView = "results" | "files" | "changes";
export function WorkspacePanel({ view, onViewChange, onClose, results, files, changes, resultCount = 0, fileCount = 0, changeCount = 0 }: {
  view: WorkspaceView; onViewChange: (view: WorkspaceView) => void; onClose: () => void;
  results: ReactNode; files: ReactNode; changes?: ReactNode; resultCount?: number; fileCount?: number; changeCount?: number;
}) {
  const id = useId();
  const tabs = ([['results', 'Results', Layers, resultCount], ['files', 'Files', FolderOpen, fileCount], ...(changes !== undefined ? [['changes', 'Changes', FileDiff, changeCount]] : [])] as Array<[WorkspaceView, string, typeof Layers, number]>);
  const order = tabs.map(([value]) => value);
  return <div className="matopt-result-workspace">
    <header className="matopt-pane-header matopt-workspace-header">
      <div className="matopt-workspace-heading"><h2>Workspace</h2></div>
      <button type="button" className="matopt-chrome-button" aria-label="Back to conversation" title="Close workspace" onClick={onClose}><ArrowLeft className="matopt-mobile-back" /><X className="matopt-desktop-close" /></button>
    </header>
    <div className="matopt-workspace-tabs" role="tablist" aria-label="Workspace views">
        {tabs.map(([value, label, Icon, count]) => <button type="button" key={value} role="tab" data-pane-heading={view === value ? "" : undefined} id={`${id}-${value}-tab`} aria-controls={`${id}-${value}`} aria-selected={view === value} tabIndex={view === value ? 0 : -1}
          onClick={() => onViewChange(value)} onKeyDown={(event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault(); const index = order.indexOf(value);
            const next = event.key === 'Home' ? order[0] : event.key === 'End' ? order[order.length - 1]
              : order[(index + (event.key === 'ArrowRight' ? 1 : order.length - 1)) % order.length];
            onViewChange(next); document.getElementById(`${id}-${next}-tab`)?.focus({ preventScroll: true });
          }}><Icon aria-hidden="true" /><span>{label}</span>{count > 0 && <span className="matopt-tab-count">{count}</span>}</button>)}
    </div>
    <div id={`${id}-results`} role="tabpanel" aria-labelledby={`${id}-results-tab`} className="matopt-workspace-view" hidden={view !== "results"}>{results}</div>
    <div id={`${id}-files`} role="tabpanel" aria-labelledby={`${id}-files-tab`} className="matopt-workspace-view" hidden={view !== "files"}>{files}</div>
    {changes !== undefined && <div id={`${id}-changes`} role="tabpanel" aria-labelledby={`${id}-changes-tab`} className="matopt-workspace-view" hidden={view !== "changes"}>{changes}</div>}
  </div>;
}

export function ResultWorkspace({ surfaces, artifacts }: { surfaces: UISurface[]; artifacts: Artifact[] }) {
  const workspace = useUiWorkspace();
  const artifactKey = (artifact: Artifact) => artifact.path || artifact.url || `${artifact.kind}:${artifact.name}`;
  const count = surfaces.length + artifacts.length;
  return <div className="matopt-results">
    {count > 0 && <div className="matopt-result-library">
      <div className="matopt-result-library-label"><span>From this conversation</span><span>{count} {count === 1 ? "result" : "results"}</span></div>
      <nav className="matopt-result-index" aria-label="Available results">
        {surfaces.map((surface) => <button type="button" key={surface.id} title={surface.title} aria-pressed={workspace.selection?.kind === "surface" && workspace.selection.surfaceId === surface.id} onClick={() => workspace.openSurface(surface)}>
          <Layers aria-hidden="true" /><span className="matopt-result-index-label"><span>{surface.title}</span><small>{surface.status === "preview" ? "Building…" : "Interactive result"}</small></span>
        </button>)}
        {artifacts.map((artifact) => <button type="button" key={artifactKey(artifact)} title={artifact.name} aria-pressed={workspace.selection?.kind === "artifact" && artifactKey(workspace.selection.artifact) === artifactKey(artifact)} onClick={() => workspace.openArtifact(artifact)}>
          <FileText aria-hidden="true" /><span className="matopt-result-index-label"><span>{artifact.name}</span><small>Output file</small></span>
        </button>)}
      </nav>
    </div>}
    <div className="matopt-result-document">{workspace.selection ? <UiCanvas /> : <div className="matopt-results-empty"><span className="matopt-workspace-empty-icon"><Layers aria-hidden="true" /></span><h3>{count ? "Choose a result to explore" : "Your work, in one place"}</h3><p>{count ? "Select an output above to open it alongside the conversation." : "Documents, charts, and interactive results will appear here as you work."}</p></div>}</div>
  </div>;
}
