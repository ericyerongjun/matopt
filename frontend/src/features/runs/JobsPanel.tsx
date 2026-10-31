/** Background shell jobs started in this conversation. */
import { useCallback } from "react";
import { Terminal } from "lucide-react";
import { runsService } from "../../services/agents.ts";
import { formatElapsed } from "../../components/WorkActivity.tsx";
import { usePolledResource } from "./usePolledResource.ts";
import "./runs.css";

export function JobsPanel({ conversationId, running, revision = 0 }: { conversationId: string | null; running: boolean; revision?: number }) {
    const load = useCallback((id: string, signal: AbortSignal) => runsService.jobs(id, signal), []);
    const state = usePolledResource(conversationId, load, (jobs) => running || Boolean(jobs?.some((job) => job.running)), revision);
    if (state.unavailable) return <p className="matopt-runs-empty" role="status">Background jobs are not available on this server.</p>;
    const jobs = state.data ?? [];
    return <div className="matopt-runs-panel">
        {state.error && <p className="matopt-runs-note" role="status">Jobs are temporarily unavailable.</p>}
        {!jobs.length ? <p className="matopt-runs-empty">{state.data || !conversationId ? "No background jobs in this chat." : "Loading jobs…"}</p>
            : <ul className="matopt-runs-list" aria-label="Background jobs">{jobs.map((job) => <li key={job.session_id} data-job-running={job.running || undefined}>
                <div className="matopt-runs-row">
                    <Terminal aria-hidden="true" />
                    <code className="matopt-runs-command" title={job.command}>{job.command || `Session ${job.session_id}`}</code>
                    <span className="matopt-runs-badge" data-tone={job.running ? "active" : job.exit_code === 0 ? "success" : job.exit_code === null ? "neutral" : "danger"}>
                        {job.running ? "Running" : job.exit_code === null ? "Ended" : `Exit ${job.exit_code}`}</span>
                </div>
                <p className="matopt-runs-meta">Session {job.session_id}{job.duration_ms > 0 ? ` · ${formatElapsed(job.duration_ms)}` : ""}
                    {job.started_at > 0 ? ` · started ${new Date(job.started_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}</p>
            </li>)}</ul>}
    </div>;
}
