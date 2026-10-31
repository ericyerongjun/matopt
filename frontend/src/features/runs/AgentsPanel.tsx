/** Sub-agents of this conversation: identity, status, task and their own final summary only. */
import { useCallback } from "react";
import { Bot } from "lucide-react";
import type { SubagentEvent } from "../../types/protocol.ts";
import { runsService, type AgentRecord } from "../../services/agents.ts";
import { InlineMathText } from "../../components/InlineMathText.tsx";
import { usePolledResource } from "./usePolledResource.ts";
import "./runs.css";

const ACTIVE = new Set(["running", "started", "queued", "pending", "waiting"]);
const LABELS: Record<string, string> = {
    started: "Running", running: "Running", queued: "Queued", pending: "Queued", waiting: "Waiting", completed: "Done", done: "Done",
    errored: "Failed", error: "Failed", failed: "Failed", interrupted: "Interrupted", closed: "Closed", stopped: "Stopped",
};
export function agentStatusLabel(status: string) { return LABELS[status] ?? (status ? status.charAt(0).toUpperCase() + status.slice(1) : "Unknown"); }
function tone(status: string) {
    return ACTIVE.has(status) ? "active" : ["completed", "done"].includes(status) ? "success" : ["errored", "error", "failed"].includes(status) ? "danger" : "neutral";
}

/** Live lifecycle events override the last fetched snapshot. */
export function mergeAgents(fetched: AgentRecord[], live: SubagentEvent[]): AgentRecord[] {
    const merged = new Map(fetched.map((agent) => [agent.id, agent]));
    for (const event of live) {
        const previous = merged.get(event.id);
        const liveStatus = event.kind === "started" ? (event.status && event.status !== "started" ? event.status : "running") : event.kind;
        // A fetched terminal state is newer than a replayed "started".
        const status = previous && !ACTIVE.has(previous.status) && ACTIVE.has(liveStatus) ? previous.status : liveStatus;
        merged.set(event.id, { id: event.id, parent_id: previous?.parent_id ?? null, path: event.path || previous?.path || "",
            nickname: event.nickname || previous?.nickname || "", role: event.role || previous?.role || "",
            status,
            task: event.task || previous?.task || "", summary: event.summary ?? previous?.summary ?? "",
            created_at: previous?.created_at ?? 0, updated_at: previous?.updated_at ?? 0 });
    }
    return [...merged.values()];
}

export function AgentsPanel({ conversationId, running, live = [], revision = 0 }: {
    conversationId: string | null; running: boolean; live?: SubagentEvent[]; revision?: number;
}) {
    const load = useCallback((id: string, signal: AbortSignal) => runsService.agents(id, signal), []);
    const state = usePolledResource(conversationId, load, (data) => running || Boolean(data?.some((agent) => ACTIVE.has(agent.status))), revision);
    const agents = mergeAgents(state.data ?? [], live);
    if (state.unavailable && !agents.length) return <p className="matopt-runs-empty" role="status">Sub-agents are not available on this server.</p>;
    return <div className="matopt-runs-panel">
        {state.error && <p className="matopt-runs-note" role="status">Sub-agents are temporarily unavailable.</p>}
        {!agents.length ? <p className="matopt-runs-empty">{state.data || !conversationId ? "No sub-agents in this chat." : "Loading sub-agents…"}</p>
            : <ul className="matopt-runs-list" aria-label="Sub-agents">{agents.map((agent) => <li key={agent.id} data-agent-status={agent.status}>
                <div className="matopt-runs-row">
                    <Bot aria-hidden="true" />
                    <span className="matopt-runs-name" title={agent.path || agent.id}>{agent.nickname || agent.path || agent.id}</span>
                    {agent.role && <span className="matopt-runs-role">{agent.role}</span>}
                    <span className="matopt-runs-badge" data-tone={tone(agent.status)}>{agentStatusLabel(agent.status)}</span>
                </div>
                {agent.task && <p className="matopt-runs-task"><InlineMathText text={agent.task} /></p>}
                {agent.summary && !ACTIVE.has(agent.status) && <div className="matopt-runs-summary"><span>Summary</span><p><InlineMathText text={agent.summary} /></p></div>}
            </li>)}</ul>}
    </div>;
}
