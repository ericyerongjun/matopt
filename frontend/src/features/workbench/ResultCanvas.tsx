import { useMemo } from "react";
import { FileText, Image } from "lucide-react";
import type { ChatMessage } from "../../types/protocol";
import { latestResultText } from "../../services/workspaceResult.ts";
import { selectOutputArtifacts } from "../../services/outputArtifacts";
import MarkdownRenderer from "../../components/MarkdownRenderer";
import { ArtifactView } from "../../components/ArtifactView";
import "./workbench.css";

export function ResultCanvas({ messages, conversationId, running, startDisabled = false, artifactsOnly = false, onOpenFiles, onStart }: {
    messages: ChatMessage[]; conversationId: string | null; running: boolean; startDisabled?: boolean; artifactsOnly?: boolean;
    onOpenFiles: () => void; onStart: (prompt: string) => void;
}) {
    const answer = latestResultText(messages);
    const artifacts = useMemo(() => selectOutputArtifacts(messages), [messages]);
    if (artifactsOnly) return <div className="result-canvas matopt-result-canvas">
        {artifacts.length ? artifacts.map((artifact, index) => <section className="canvas-artifact" key={artifact.digest ?? artifact.path ?? `${artifact.name}-${index}`}>
            <header><Image size={20} strokeWidth={1.8} /><span>{artifact.name}</span></header>
            <ArtifactView artifact={artifact} conversationId={conversationId} />
        </section>) : <div className="canvas-empty">
            <p>Figures and plots will appear here.</p>
            <button type="button" className="workspace-button" onClick={onOpenFiles}><FileText size={20} strokeWidth={1.8} />Browse files</button></div>}
    </div>;
    if (!answer) return <div className="canvas-empty matopt-result-canvas">
        <p>{running ? "Your result will appear here when it is ready." : "Start with a plot or an experiment."}</p>
        {!running && <div className="canvas-starters">
            <button type="button" disabled={startDisabled} onClick={() => onStart("Plot sin(x)/x and its Taylor approximation, then explain the approximation error.")}>Explore a function</button>
            <button type="button" disabled={startDisabled} onClick={() => onStart("Create a small reproducible statistical simulation and explain the uncertainty in the estimate.")}>Run an experiment</button>
        </div>}
    </div>;
    return <div className="result-canvas matopt-result-canvas">
        <article className="canvas-document">
            <MarkdownRenderer content={answer} conversationId={conversationId} />
        </article>
        {artifacts.map((artifact, index) => <section className="canvas-artifact" key={artifact.digest ?? artifact.path ?? `${artifact.name}-${index}`}>
            <header><Image size={20} strokeWidth={1.8} /><span>{artifact.name}</span></header><ArtifactView artifact={artifact} conversationId={conversationId} />
        </section>)}
    </div>;
}
