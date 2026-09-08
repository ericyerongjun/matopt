/**
 * Cursor-style agent pane: dense timeline of grouped tool steps.
 * Collapses to a rail. Figures stay clipped so they cannot resize the layout.
 */

import React, { useMemo, useState } from "react";
import { Check, ChevronDown, ChevronRight, Loader2, PanelRightClose, ShieldAlert } from "lucide-react";
import type { AssistantContent, ChatMessage, Part, PlanStep } from "../types/protocol";
import { isAssistantContent } from "../types/protocol";
import { ArtifactView, type ToolPart } from "./ToolCallCard";
import "./AnalysisPanel.css";

interface Props {
    messages: ChatMessage[];
    conversationId: string | null;
    open: boolean;
    onToggle: () => void;
}

interface ToolGroup {
    name: string;
    items: ToolPart[];
}

function collect(messages: ChatMessage[]) {
    const tools: ToolPart[] = [];
    const monitors: Extract<Part, { type: "monitor" }>[] = [];
    let plan: PlanStep[] = [];
    for (const msg of messages) {
        if (msg.role !== "assistant" || !isAssistantContent(msg.content)) continue;
        const content = msg.content as AssistantContent;
        if (content.plan?.length) plan = content.plan;
        for (const part of content.parts) {
            if (part.type === "tool_call") tools.push(part);
            if (part.type === "monitor") monitors.push(part);
        }
    }
    return { tools, monitors, plan };
}

function groupTools(tools: ToolPart[]): ToolGroup[] {
    const groups: ToolGroup[] = [];
    for (const tool of tools) {
        const last = groups[groups.length - 1];
        if (last && last.name === tool.name) last.items.push(tool);
        else groups.push({ name: tool.name, items: [tool] });
    }
    return groups;
}

function verbFor(name: string, running: boolean) {
    if (running) return "Running";
    if (name === "web_search" || name === "web_fetch") return "Searched";
    if (name === "python" || name === "bash") return "Ran";
    if (name.includes("write") || name.includes("edit")) return "Edited";
    if (name.startsWith("read") || name === "list_files" || name === "grep") return "Read";
    if (name.includes("skill")) return "Loaded";
    if (name === "update_plan") return "Updated";
    return "Called";
}

export default function AnalysisPanel({ messages, conversationId, open, onToggle }: Props) {
    const { tools, monitors, plan } = collect(messages);
    const groups = useMemo(() => groupTools(tools), [tools]);
    const running = tools.some((t) => t.status === "running");
    const artifacts = tools.flatMap((t) => t.result?.artifacts ?? []).filter((a) =>
        ["image", "plotly", "html", "html_file"].includes(String(a.kind))
    );
    const flagged = monitors.filter((m) => m.level !== "ok");
    const [openKey, setOpenKey] = useState<string | null>(null);

    if (!open) {
        return (
            <aside className="agent-pane agent-pane--rail" aria-label="Work">
                <button type="button" className="agent-pane__rail-btn" onClick={onToggle} title="Open work">
                    {running ? <Loader2 size={13} className="spin" /> : <span>{tools.length || ""}</span>}
                    <span className="agent-pane__rail-label">Work</span>
                </button>
            </aside>
        );
    }

    return (
        <aside className="agent-pane" aria-label="Work">
            <header className="agent-pane__head">
                <div className="agent-pane__title">
                    <span>Work</span>
                    {tools.length > 0 && <span className="agent-pane__count">{tools.length}</span>}
                    {running && <span className="agent-pane__live">live</span>}
                </div>
                <button type="button" className="icon-btn" onClick={onToggle} title="Collapse work">
                    <PanelRightClose size={15} />
                </button>
            </header>

            <div className="agent-pane__body">
                {flagged.length > 0 && (
                    <div className="agent-pane__flags">
                        {flagged.map((m, i) => (
                            <div key={`${m.tool}-${i}`} className={`agent-flag agent-flag--${m.level}`}>
                                <ShieldAlert size={12} />
                                <span>{m.summary}</span>
                            </div>
                        ))}
                    </div>
                )}

                {plan.length > 0 && (
                    <section className="agent-section">
                        <h3>Plan</h3>
                        <ol className="agent-plan">
                            {plan.map((step, i) => (
                                <li key={i} className={`agent-plan__item agent-plan__item--${step.status}`}>
                                    {step.status === "completed" ? <Check size={11} /> : <ChevronRight size={11} />}
                                    <span>{step.content}</span>
                                </li>
                            ))}
                        </ol>
                    </section>
                )}

                <section className="agent-section">
                    <h3>Steps</h3>
                    {groups.length === 0 ? (
                        <p className="agent-pane__empty">Tool calls land here.</p>
                    ) : (
                        <ol className="agent-tree">
                            {groups.map((group, i) => {
                                const key = `${group.name}-${i}`;
                                const last = group.items[group.items.length - 1];
                                const isRunning = group.items.some((t) => t.status === "running");
                                const failed = group.items.some((t) => t.status === "error");
                                const expanded = openKey === key;
                                return (
                                    <li key={key} className={`agent-tree__item${isRunning ? " is-live" : ""}${failed ? " is-fail" : ""}`}>
                                        <button
                                            type="button"
                                            className="agent-tree__row"
                                            onClick={() => setOpenKey(expanded ? null : key)}
                                        >
                                            <span className="agent-tree__twist">
                                                {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                                            </span>
                                            <span className="agent-tree__mark">
                                                {isRunning ? (
                                                    <Loader2 size={11} className="spin" />
                                                ) : failed ? (
                                                    <span className="agent-tree__x">×</span>
                                                ) : (
                                                    <Check size={11} />
                                                )}
                                            </span>
                                            <span className="agent-tree__verb">{verbFor(group.name, isRunning)}</span>
                                            <span className="agent-tree__name">{group.name}</span>
                                            {group.items.length > 1 && (
                                                <span className="agent-tree__n">×{group.items.length}</span>
                                            )}
                                        </button>
                                        {expanded && (
                                            <ol className="agent-tree__kids">
                                                {group.items.map((tool, j) => (
                                                    <li key={tool.id || j} className={`agent-tree__kid agent-tree__kid--${tool.status}`}>
                                                        {tool.status === "running" ? "running" : tool.status === "error" ? "failed" : "done"}
                                                        {tool.result?.error ? ` — ${tool.result.error}` : ""}
                                                    </li>
                                                ))}
                                            </ol>
                                        )}
                                        {last?.result?.error && !expanded && (
                                            <div className="agent-tree__err">{last.result.error.split("\n")[0].slice(0, 120)}</div>
                                        )}
                                    </li>
                                );
                            })}
                        </ol>
                    )}
                </section>

                {artifacts.length > 0 && (
                    <section className="agent-section">
                        <h3>Output</h3>
                        <div className="agent-pane__arts">
                            {artifacts.map((art, i) => (
                                <div key={`${art.name}-${i}`} className="agent-pane__art">
                                    <ArtifactView artifact={art} conversationId={conversationId} />
                                </div>
                            ))}
                        </div>
                    </section>
                )}
            </div>
        </aside>
    );
}
