import type { ActivityItem, Artifact, AssistantContent, Part, ProgressUpdate, ResultBlock, TranscriptEntry } from "../types/protocol.ts";
import { readResultBlock, readPublicProgress, readPublicMilestones } from "../types/protocol.ts";
import type { UISurface } from "../types/ui.ts";

const WORK_LABELS = new Set([
    "Working", "Reading uploaded file", "Reading files", "Searching sources", "Computing",
    "Creating interactive view", "Checking answer", "Correcting answer", "Preparing final answer",
]);
const VERIFICATION_STATUSES = new Set([
    "not_requested", "skipped", "correct", "incorrect", "uncertain", "limit_reached", "error",
]);

export interface PresentedWork {
    status: "running" | "paused" | "complete" | "stopped" | "error";
    toolCount: number;
    verification: { status: string; rounds: number };
    steps: Array<{ label: string; status: "running" | "complete" | "error" }>;
    items: ActivityItem[];
    progress: ProgressUpdate | null;
    milestones: ProgressUpdate[];
    startedAt?: number;
    finishedAt?: number;
    durationMs?: number;
    /** Narration and actions, Claude Code style; empty for older messages. */
    transcript: TranscriptEntry[];
}

export interface AssistantPresentation {
    answer: string;
    notice: string | null;
    earlierDrafts: string[];
    resultBlocks: ResultBlock[];
    earlierResults: ResultBlock[];
    surfaces: UISurface[];
    artifacts: Artifact[];
    options: string[];
    work: PresentedWork | null;
}

/** Tool names are mapped to fixed labels; arguments, stdout and errors never enter the presentation. */
export function safeWorkLabel(name: string): string {
    if (/^(ocr|parse_document|read_attachment|document_ocr)$/.test(name)) return "Reading uploaded file";
    if (/^(read_file|list_files|glob|grep|search_files|search_skills|load_skill|read_skill)$/.test(name)) return "Reading files";
    if (/^(web_search|web_fetch|search_web|fetch_url)$/.test(name)) return "Searching sources";
    if (/^(python|bash|shell|execute_python|run_python|run_code|check_equivalence|wolfram_alpha|data_agent)$/.test(name)) return "Computing";
    if (/^(render_ui|create_ui|update_ui|create_ui_app|register_ui_action)$/.test(name)) return "Creating interactive view";
    return "Working";
}

function count(value: unknown): number {
    return typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
}

const ACTIVITY_STATUSES = new Set(["queued", "running", "completed", "failed", "cancelled", "paused"]);
const ACTIVITY_KINDS = new Set(["phase", "tool", "agent", "control"]);
const ACTIVITY_PHASES = new Set(["work", "check", "final"]);
const timestamp = (value: unknown): number | undefined => typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;
const publicText = (value: unknown, limit = 500): string => typeof value === "string" ? value.slice(0, limit) : "";

/** Pick only the public schema; never spread arbitrary tool metadata into UI state. */
function activityItems(content: AssistantContent, workStatus: PresentedWork["status"]): ActivityItem[] {
    const items = new Map<string, ActivityItem>();
    for (const item of content.work?.items ?? []) {
        if (!item || typeof item.id !== "string" || !ACTIVITY_KINDS.has(item.kind) || !ACTIVITY_PHASES.has(item.phase) || !ACTIVITY_STATUSES.has(item.status)) continue;
        let status = item.status;
        if (workStatus === "paused" && status === "running") status = "paused";
        if (!["running", "paused"].includes(workStatus) && ["running", "queued", "paused"].includes(status)) status = workStatus === "complete" ? "completed" : "cancelled";
        const progress = item.progress && typeof item.progress.completed === "number" && Number.isFinite(item.progress.completed) && item.progress.completed >= 0 ? {
            completed: item.progress.completed,
            ...(typeof item.progress.total === "number" && Number.isFinite(item.progress.total) && item.progress.total >= item.progress.completed && item.progress.total > 0 ? { total: item.progress.total } : {}),
            ...(typeof item.progress.unit === "string" ? { unit: publicText(item.progress.unit, 40) } : {}),
        } : undefined;
        const projected: ActivityItem = {
            id: item.id, parent_id: typeof item.parent_id === "string" ? item.parent_id : null, seq: count(item.seq),
            kind: item.kind, phase: item.phase, label: publicText(item.label, 120) || "Working", purpose: publicText(item.purpose), status,
            started_at: timestamp(item.started_at) ?? null, updated_at: status === "paused" ? Math.max(timestamp(item.updated_at) ?? 0, timestamp(content.work?.current_progress?.created_at) ?? 0) : timestamp(item.updated_at) ?? 0, finished_at: timestamp(item.finished_at) ?? null,
            summary: publicText(item.summary), tool: publicText(item.tool, 80), attempt: Math.max(1, count(item.attempt)), progress,
            outputs: (Array.isArray(item.outputs) ? item.outputs : []).filter((output) => output && ["file", "source", "artifact", "ui"].includes(output.kind)).map((output) => ({
                kind: output.kind, label: publicText(output.label, 160),
                ...(typeof output.url === "string" ? { url: output.url } : {}), ...(typeof output.path === "string" ? { path: output.path } : {}),
                ...(typeof output.surface_id === "string" ? { surface_id: output.surface_id } : {}),
            })),
        };
        if (!items.has(item.id) || items.get(item.id)!.seq <= projected.seq) items.set(item.id, projected);
    }
    return [...items.values()].sort((a, b) => (a.started_at ?? a.updated_at) - (b.started_at ?? b.updated_at) || a.seq - b.seq);
}

function workFor(content: AssistantContent, streaming: boolean): PresentedWork | null {
    const status = streaming || content.status === "streaming" ? "running" : content.status;
    const publicWork = content.work;
    if (publicWork) {
        const workStatus = content.status === "stopped" || content.status === "error" ? content.status
            : !streaming && content.status === "complete" && ["running", "paused"].includes(publicWork.status) ? "complete"
            : ["running", "paused", "stopped", "error"].includes(publicWork.status) ? publicWork.status : "complete";
        const steps = (Array.isArray(publicWork.steps) ? publicWork.steps : []).map((step) => ({
            label: WORK_LABELS.has(step?.label) ? step.label : "Working",
            status: step?.status === "running" && workStatus !== "running" ? workStatus === "complete" ? "complete" as const : "error" as const
                : step?.status === "running" || step?.status === "error" ? step.status : "complete" as const,
        }));
        return {
            status: workStatus,
            toolCount: count(publicWork.tool_count),
            verification: {
                status: VERIFICATION_STATUSES.has(publicWork.verification?.status) ? publicWork.verification.status : "not_requested",
                rounds: count(publicWork.verification?.rounds),
            },
            steps,
            items: activityItems(content, workStatus),
            progress: readPublicProgress(publicWork.current_progress) ?? null,
            milestones: readPublicMilestones(publicWork.milestones),
            startedAt: timestamp(publicWork.started_at), finishedAt: timestamp(publicWork.finished_at), durationMs: timestamp(publicWork.duration_ms),
            transcript: readTranscript(content.transcript, workStatus),
        };
    }

    const steps: PresentedWork["steps"] = [];
    let toolCount = 0;
    let verification = { status: "not_requested", rounds: 0 };
    for (const part of content.parts) {
        if (part.type === "tool_call") {
            toolCount++;
            steps.push({
                label: part.phase === "verify" ? "Checking answer" : safeWorkLabel(part.name),
                status: part.status === "error" || part.status === "stopped" ? "error" : part.status === "running" && status === "running" ? "running" : "complete",
            });
        } else if (part.type === "phase") {
            const label = part.phase === "verify" ? "Checking answer" : "Correcting answer";
            if (steps.at(-1)?.label !== label) steps.push({ label, status: "complete" });
            if (part.phase === "verify") verification = { status: "uncertain", rounds: Math.max(verification.rounds, count(part.round)) };
        } else if (part.type === "verification") {
            verification = { status: VERIFICATION_STATUSES.has(part.verdict) ? part.verdict : "uncertain", rounds: Math.max(verification.rounds, count(part.round)) };
            if (!steps.some((step) => step.label === "Checking answer")) steps.push({ label: "Checking answer", status: part.verdict === "error" ? "error" : "complete" });
        }
    }
    if (!steps.length && (status === "running" || content.parts.some((part) => part.type === "reasoning"))) steps.push({ label: "Working", status: status === "running" ? "running" : "complete" });
    if (!steps.length) return null;
    return { status, toolCount: Math.max(toolCount, count(content.stats?.tool_calls)), verification, steps, items: [], progress: null, milestones: [],
        transcript: readTranscript(content.transcript, status) };
}

function legacyAnswer(parts: Part[]): string {
    // Text before a tool call is task narration. Only the last completed answer
    // block survives; verifier text and previous revisions stay out of chat.
    for (let index = parts.length - 1; index >= 0; index--) {
        const part = parts[index];
        if (part.type !== "text" || part.phase === "verify" || !part.text.trim()) continue;
        const interrupted = parts.slice(index + 1).some((later) =>
            (later.type === "tool_call" && later.phase !== "verify") || (later.type === "phase" && later.phase === "revise"));
        return interrupted ? "" : part.text;
    }
    return "";
}

function uniqueArtifacts(artifacts: Artifact[]): Artifact[] {
    const seen = new Set<string>();
    return artifacts.slice().reverse().filter((artifact, index) => {
        const key = artifact.digest || artifact.path || artifact.url || `${artifact.kind}:${artifact.name}:${index}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    }).reverse();
}

/** A single presentation boundary shared by chat, copy, and the Activity timeline. */
export function projectAssistantPresentation(content: AssistantContent, streaming = false): AssistantPresentation {
    const isPublic = content.presentation === "final";
    const ready = isPublic || (!streaming && content.status !== "streaming");
    const answer = !ready ? "" : isPublic
        ? content.parts.flatMap((part) => part.type === "text" && part.phase !== "verify" ? [part.text] : []).join("\n\n")
        : legacyAnswer(content.parts);
    const surfaces: UISurface[] = [];
    const artifacts: Artifact[] = [];
    const blocks = new Map<string, ResultBlock>();
    for (const part of content.parts) {
        if (part.type === "result_block") {
            const block = readResultBlock(part.block);
            if (block && (!blocks.has(block.id) || blocks.get(block.id)!.revision < block.revision)) blocks.set(block.id, block);
        }
        const publicPreview = streaming && Array.isArray(content.work?.items);
        if (part.type === "ui" && ((ready || publicPreview) && part.surface.status === "committed" || publicPreview && part.surface.kind === "native" && part.surface.status === "preview")) {
            const previous = surfaces.findIndex((surface) => surface.id === part.surface.id);
            if (previous < 0) surfaces.push(part.surface);
            else if (surfaces[previous].revision <= part.surface.revision) surfaces[previous] = part.surface;
        } else if (ready && part.type === "artifact") artifacts.push(part.artifact);
        else if (ready && !isPublic && part.type === "tool_call" && part.phase !== "verify" && part.phase !== "revise"
            && !["parse_document", "read_file", "list_files", "grep"].includes(part.name)) {
            artifacts.push(...part.result?.artifacts ?? []);
        }
    }
    return {
        answer,
        notice: isPublic && typeof content.notice === "string" ? content.notice : null,
        earlierDrafts: isPublic && Array.isArray(content.earlier_drafts) ? content.earlier_drafts.filter((draft) => typeof draft === "string" && draft.trim() && draft !== answer) : [],
        resultBlocks: [...blocks.values()].filter((block) => block.status === "accepted" || block.status === "provisional"),
        earlierResults: [...blocks.values()].filter((block) => block.status === "superseded"),
        surfaces,
        artifacts: uniqueArtifacts(artifacts),
        options: ready && !streaming && content.status !== "streaming" ? content.parts.flatMap((part) => part.type === "options" ? part.items : []) : [],
        work: workFor(content, streaming),
    };
}

const TRANSCRIPT_STATUSES = new Set(["running", "done", "error", "stopped"]);

/** Re-validate transcript rows; rows still running in a finished turn are shown as stopped. */
export function readTranscript(value: unknown, workStatus: PresentedWork["status"]): TranscriptEntry[] {
    if (!Array.isArray(value)) return [];
    const live = workStatus === "running" || workStatus === "paused";
    return value.flatMap((raw): TranscriptEntry[] => {
        if (!raw || typeof raw !== "object") return [];
        const entry = raw as Record<string, unknown>;
        const order = entry.order;
        if ((entry.kind !== "note" && entry.kind !== "action") || typeof entry.id !== "string"
            || !Array.isArray(order) || order.length !== 2 || !order.every((n) => typeof n === "number")) return [];
        const text = (key: string) => typeof entry[key] === "string" ? entry[key] as string : undefined;
        const status = TRANSCRIPT_STATUSES.has(entry.status as string) ? entry.status as TranscriptEntry["status"] : undefined;
        return [{ id: entry.id, kind: entry.kind, order: [order[0] as number, order[1] as number], text: text("text"), tool: text("tool"),
            title: text("title"), subject: text("subject"), language: text("language"), input: text("input"), output: text("output"),
            status: status === "running" && !live ? "stopped" : status }];
    });
}
