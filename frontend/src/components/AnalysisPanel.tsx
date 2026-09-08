/**
 * Compact work pane — Cursor / Claude Code / Codex style.
 * Timeline of actions, plan, plots, and monitor chips. Collapses to a rail.
 */

import React from "react";
import { Check, ChevronRight, Globe, Loader2, PanelRightClose, ShieldAlert } from "lucide-react";
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

function labelFor(name: string) {
    if (name === "web_search" || name === "web_fetch") return "Web";
    if (name === "python" || name === "bash") return "Run";
    if (name === "lambda_data_agent") return "Data";
    if (name === "update_plan") return "Plan";
    if (name.startsWith("read") || name === "list_files" || name === "grep") return "Read";
    if (name.includes("write") || name.includes("edit")) return "Edit";
    if (name.includes("skill")) return "Skill";
    return name.replace(/_/g, " ");
}

export default function AnalysisPanel({ messages, conversationId, open, onToggle }: Props) {
    const { tools, monitors, plan } = collect(messages);
    const running = tools.some((t) => t.status === "running");
    const artifacts = tools.flatMap((t) => t.result?.artifacts ?? []).filter((a) =>
        ["image", "plotly", "html", "html_file"].includes(String(a.kind))
    );
    const flagged = monitors.filter((m) => m.level !== "ok");

    if (!open) {
        return (
            <aside className="work-panel work-panel--rail" aria-label="Work">
                <button type="button" className="work-panel__rail-btn" onClick={onToggle} title="Open work">
                    <span className="work-panel__rail-count">{tools.length || "·"}</span>
                    <span className="work-panel__rail-label">Work</span>
                    {running && <Loader2 size={12} className="spin" />}
                </button>
            </aside>
        );
    }

    return (
        <aside className="work-panel" aria-label="Work">
            <header className="work-panel__head">
                <div>
                    <strong>Work</strong>
                    <span className="work-panel__meta">
                        {tools.length} {tools.length === 1 ? "action" : "actions"}
                        {running ? " · live" : ""}
                    </span>
                </div>
                <button type="button" className="icon-btn" onClick={onToggle} title="Collapse work">
                    <PanelRightClose size={16} />
                </button>
            </header>

            {flagged.length > 0 && (
                <div className="work-panel__monitor">
                    {flagged.map((m, i) => (
                        <div key={`${m.tool}-${i}`} className={`work-flag work-flag--${m.level}`}>
                            <ShieldAlert size={13} />
                            <span>{m.summary}</span>
                        </div>
                    ))}
                </div>
            )}

            {plan.length > 0 && (
                <section className="work-panel__section">
                    <h3>Plan</h3>
                    <ol className="work-plan">
                        {plan.map((step, i) => (
                            <li key={i} className={`work-plan__item work-plan__item--${step.status}`}>
                                {step.status === "completed" ? <Check size={12} /> : <ChevronRight size={12} />}
                                {step.content}
                            </li>
                        ))}
                    </ol>
                </section>
            )}

            <section className="work-panel__section">
                <h3>Actions</h3>
                {tools.length === 0 ? (
                    <p className="work-panel__empty">Tool calls land here, compacted — not in the chat.</p>
                ) : (
                    <ol className="work-graph">
                        {tools.map((tool, i) => (
                            <li key={tool.id || i} className={`work-graph__node work-graph__node--${tool.status}`}>
                                <span className="work-graph__dot">
                                    {tool.status === "running" ? (
                                        <Loader2 size={11} className="spin" />
                                    ) : tool.name === "web_search" || tool.name === "web_fetch" ? (
                                        <Globe size={11} />
                                    ) : (
                                        <Check size={11} />
                                    )}
                                </span>
                                <div>
                                    <div className="work-graph__name">{labelFor(tool.name)}</div>
                                    <div className="work-graph__sub">{tool.name}</div>
                                </div>
                            </li>
                        ))}
                    </ol>
                )}
            </section>

            {artifacts.length > 0 && (
                <section className="work-panel__section">
                    <h3>Figures</h3>
                    <div className="work-panel__arts">
                        {artifacts.map((art, i) => (
                            <ArtifactView key={`${art.name}-${i}`} artifact={art} conversationId={conversationId} />
                        ))}
                    </div>
                </section>
            )}
        </aside>
    );
}
