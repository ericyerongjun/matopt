/** Sub-agents, background jobs and approval decisions for a conversation's runs. */
import apiClient from "./api.ts";

export interface AgentRecord {
    id: string;
    parent_id: string | null;
    path: string;
    nickname: string;
    role: string;
    status: string;
    task: string;
    summary: string;
    created_at: number;
    updated_at: number;
}
export interface JobRecord {
    session_id: string;
    command: string;
    running: boolean;
    exit_code: number | null;
    started_at: number;
    duration_ms: number;
}

const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const str = (value: unknown) => typeof value === "string" ? value : "";
const num = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : 0;

export function readAgent(value: unknown): AgentRecord | null {
    const agent = record(value);
    if (!str(agent.id)) return null;
    return { id: str(agent.id), parent_id: str(agent.parent_id) || null, path: str(agent.path), nickname: str(agent.nickname), role: str(agent.role),
        status: str(agent.status) || "unknown", task: str(agent.task).slice(0, 500), summary: str(agent.summary).slice(0, 2000),
        created_at: num(agent.created_at), updated_at: num(agent.updated_at) };
}

export function readJob(value: unknown): JobRecord | null {
    const job = record(value);
    const id = typeof job.session_id === "number" ? String(job.session_id) : str(job.session_id);
    if (!id) return null;
    return { session_id: id, command: str(job.command), running: job.running === true,
        exit_code: typeof job.exit_code === "number" ? job.exit_code : null, started_at: num(job.started_at), duration_ms: num(job.duration_ms) };
}

const base = (id: string) => `/conversations/${encodeURIComponent(id)}`;
export const runsService = {
    async agents(conversationId: string, signal?: AbortSignal): Promise<AgentRecord[]> {
        const data = record((await apiClient.get(`${base(conversationId)}/agents`, { signal })).data);
        return (Array.isArray(data.agents) ? data.agents : []).map(readAgent).filter((agent): agent is AgentRecord => Boolean(agent));
    },
    async jobs(conversationId: string, signal?: AbortSignal): Promise<JobRecord[]> {
        const data = record((await apiClient.get(`${base(conversationId)}/jobs`, { signal })).data);
        return (Array.isArray(data.jobs) ? data.jobs : []).map(readJob).filter((job): job is JobRecord => Boolean(job));
    },
    async decide(runId: string, approvalId: string, decision: "allow" | "deny") {
        await apiClient.post(`/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}`, { decision });
    },
};
