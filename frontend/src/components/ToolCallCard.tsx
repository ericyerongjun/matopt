/**
 * Consumer-facing tool step — ChatGPT "Analyzed" style.
 * Never shows model-generated source, commands, or raw stdout.
 */

import React, { useEffect, useState } from "react";
import { Check, Loader2 } from "lucide-react";
import PlotRenderer from "./PlotRenderer";
import type { Artifact, Part } from "../types/protocol";
import "./ToolCallCard.css";

export type ToolPart = Extract<Part, { type: "tool_call" }>;

export const SILENT_TOOLS = new Set([
    "read_file",
    "write_file",
    "edit_file",
    "list_files",
    "grep",
    "update_plan",
    "wolfram_alpha",
    "check_equivalence",
    "load_skill",
    "search_skills",
]);

export const CODE_TOOLS = new Set(["python", "bash"]);

export function hasDisplayArtifact(part: ToolPart): boolean {
    return (part.result?.artifacts ?? []).some((a) =>
        ["image", "plotly", "html", "html_file"].includes(String(a.kind))
    );
}

function fileUrl(conversationId: string | null | undefined, artifact: Artifact): string | undefined {
    if (artifact.url) return artifact.url;
    if (conversationId && artifact.path) return `/api/files/${conversationId}/${artifact.path}`;
    return undefined;
}

export function ArtifactView({ artifact, conversationId }: { artifact: Artifact; conversationId?: string | null }) {
    const url = fileUrl(conversationId, artifact);
    const [fetched, setFetched] = useState<unknown>(null);

    useEffect(() => {
        if (artifact.kind === "plotly" && !artifact.data && url) {
            fetch(url)
                .then((r) => r.json())
                .then(setFetched)
                .catch(() => setFetched(null));
        }
    }, [artifact.kind, artifact.data, url]);

    if (artifact.kind === "image") {
        return (
            <figure className="result-figure">
                <img src={url} alt="" className="result-figure__img" />
            </figure>
        );
    }
    if (artifact.kind === "plotly") {
        const figure = (artifact.data || fetched) as { data?: unknown[] } | null;
        if (figure && Array.isArray(figure.data)) {
            return (
                <div className="result-figure">
                    <PlotRenderer figure={figure as { data?: Plotly.Data[] }} />
                </div>
            );
        }
        return null;
    }
    if (artifact.kind === "html" || artifact.kind === "html_file") {
        const html = typeof artifact.data === "string" ? artifact.data : "";
        if (html) {
            return <iframe className="result-figure__table" title={artifact.name} sandbox="" srcDoc={html} />;
        }
        if (url) return <iframe className="result-figure__table" title={artifact.name} src={url} />;
    }
    if (url && artifact.kind !== "json") {
        return (
            <a className="result-file" href={url} target="_blank" rel="noreferrer">
                {artifact.name}
            </a>
        );
    }
    return null;
}

export function AnalysisTrace({
    parts,
    conversationId,
}: {
    parts: ToolPart[];
    conversationId?: string | null;
}) {
    const running = parts.some((p) => p.status === "running");
    const anyOk = parts.some((p) => p.status === "completed");
    const artifacts = parts.flatMap((p) => p.result?.artifacts ?? []);
    const failed = !running && !anyOk && parts.length > 0 && parts.every((p) => p.status === "error" || p.status === "stopped");

    return (
        <div className="work-step">
            <div className={`work-step__chip ${running ? "work-step__chip--live" : ""} ${failed ? "work-step__chip--fail" : ""}`}>
                {running ? <Loader2 size={13} className="spin" /> : <Check size={13} />}
                {running ? "Analyzing" : failed ? "Couldn’t finish analysis" : "Analyzed"}
            </div>
            {artifacts.map((art, i) => (
                <ArtifactView key={`${art.name}-${i}`} artifact={art} conversationId={conversationId} />
            ))}
        </div>
    );
}

export default function ToolCallCard({
    part,
    conversationId,
}: {
    part: ToolPart;
    conversationId?: string | null;
}) {
    if (SILENT_TOOLS.has(part.name) || CODE_TOOLS.has(part.name)) return null;
    return <AnalysisTrace parts={[part]} conversationId={conversationId} />;
}
