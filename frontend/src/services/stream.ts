/**
 * SSE client + reducer for POST /api/chat/stream.
 *
 * Frames are `data: <json>\n\n`. Keepalive is ignored. `done` / `stopped` /
 * `error` end the stream. The reducer builds ordered message parts the same
 * way PROTOCOL.md describes, so a mid-stream UI and a persisted message share
 * one renderer.
 */

import type {
    ActivityItem,
    AnswerMode,
    ChatMode,
    ApprovalRequest,
    ApprovalResolution,
    ChangedFile,
    GuardianReview,
    SubagentEvent,
    WorkspaceChanges,
    ProgressUpdate,
    AssistantContent,
    Attachment,
    Part,
    PlanStep,
    StreamEvent,
    ToolResult,
    TranscriptEntry,
    Usage,
    WorkSummary,
} from "../types/protocol";
import type { UISurface } from "../types/ui";
import { readResultBlock, readPublicProgress, readPublicMilestones } from "../types/protocol.ts";

export interface ChatStreamBody {
    conversation_id?: string;
    /** Stable submission identity: retrying a POST never starts the turn twice. */
    client_run_id?: string;
    message: string;
    attachments?: Attachment[];
    /** Reply language code (see /api/speech/status). */
    language?: string;
    provider?: string | null;
    model?: string | null;
    reasoning?: string | null;
    verify?: boolean;
    strict_final?: boolean;
    temperature?: number;
    skills?: string[];
    tools?: string[];
    /** Defaults to automatic availability; false disables web tools for this turn. */
    web_search?: boolean;
    /** Mat, Opt or Auto. Always sent by this client; omitted means the legacy full toolset. */
    mode?: ChatMode;
    /** Advertise this client's renderer contract. The server gates UI tools. */
    ui_version?: 1;
}

export interface StreamState {
    runId?: string;
    lastSeq?: number;
    paused?: boolean;
    pauseRequested?: boolean;
    controlStatus?: string | null;
    conversationId: string | null;
    title: string | null;
    messageId: string | null;
    provider: string;
    model: string;
    reasoning: string;
    parts: Part[];
    plan: PlanStep[];
    usage: Usage;
    statusLine: string | null;
    status: "streaming" | "complete" | "stopped" | "error";
    error: string | null;
    phase: "answer" | "verify" | "revise";
    presentation?: "final";
    work?: WorkSummary;
    earlierDrafts?: string[];
    notice?: string;
    transcript?: TranscriptEntry[];
    /** Harness turn of this answer (from `workspace_changes`, `message_end` or `done`). */
    turn?: number;
    changes?: WorkspaceChanges;
    /** Latest public state of each sub-agent, in first-seen order. */
    subagents?: SubagentEvent[];
    /** Approval requests still waiting for a decision. */
    approvals?: ApprovalRequest[];
    /** Recent decisions, newest last; used to settle a card the user just answered. */
    resolvedApprovals?: ApprovalResolution[];
    guardianReviews?: GuardianReview[];
    mode?: AnswerMode;
}

export function emptyStreamState(): StreamState {
    return {
        conversationId: null,
        title: null,
        messageId: null,
        provider: "",
        model: "",
        reasoning: "",
        parts: [],
        plan: [],
        usage: {},
        statusLine: null,
        status: "streaming",
        error: null,
        phase: "answer",
    };
}

export function assistantFromStream(state: StreamState): AssistantContent {
    return {
        parts: state.parts,
        model: state.model,
        provider: state.provider,
        reasoning: state.reasoning,
        usage: state.usage,
        status: state.status === "streaming" ? "streaming" : state.status,
        plan: state.plan,
        stats: { llm_calls: 0, tool_calls: state.work?.tool_count ?? state.parts.filter((p) => p.type === "tool_call").length },
        presentation: state.presentation,
        work: state.work,
        earlier_drafts: state.earlierDrafts,
        transcript: state.transcript,
        notice: state.notice,
        ...(state.turn !== undefined ? { turn: state.turn } : {}),
        ...(state.changes ? { changes: state.changes } : {}),
        ...(state.guardianReviews?.length ? { guardian_reviews: state.guardianReviews } : {}),
        ...(state.mode ? { mode: state.mode } : {}),
    };
}

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : {};
}

const WORK_LABELS = new Set(["Working", "Reading uploaded file", "Reading files", "Searching sources", "Computing", "Creating interactive view", "Checking answer", "Correcting answer", "Preparing final answer"]);
function readWork(value: unknown): WorkSummary | undefined {
    const work = asRecord(value);
    if (!["running", "paused", "complete", "stopped", "error"].includes(String(work.status))) return undefined;
    const verification = asRecord(work.verification);
    const count = (v: unknown) => typeof v === "number" && Number.isFinite(v) ? Math.max(0, Math.floor(v)) : 0;
    return {
        status: work.status as WorkSummary["status"],
        tool_count: count(work.tool_count),
        verification: {
            status: ["not_requested", "skipped", "correct", "incorrect", "uncertain", "limit_reached", "error"].includes(String(verification.status))
                ? verification.status as WorkSummary["verification"]["status"] : "not_requested",
            rounds: count(verification.rounds),
        },
        steps: (Array.isArray(work.steps) ? work.steps : []).slice(0, 200).map((value) => {
            const step = asRecord(value);
            return { label: WORK_LABELS.has(String(step.label)) ? String(step.label) : "Working",
                status: step.status === "running" || step.status === "error" ? step.status : "complete" };
        }),
        items: (Array.isArray(work.items) ? work.items : []).map(readActivity).filter((item): item is ActivityItem => Boolean(item)),
        current_progress: readProgress(work.current_progress) ?? null,
        milestones: readPublicMilestones(work.milestones),
        ...(typeof work.started_at === "number" ? { started_at: work.started_at } : {}),
        ...(typeof work.finished_at === "number" ? { finished_at: work.finished_at } : {}),
        ...(typeof work.duration_ms === "number" ? { duration_ms: count(work.duration_ms) } : {}),
    };
}

function readProgress(value: unknown): ProgressUpdate | undefined {
    return readPublicProgress(value);
}

function readActivity(value: unknown): ActivityItem | undefined {
    const item = asRecord(value);
    if (typeof item.id !== "string" || typeof item.label !== "string"
        || !["phase", "tool", "agent", "control"].includes(String(item.kind))
        || !["queued", "running", "completed", "failed", "cancelled", "paused"].includes(String(item.status))) return undefined;
    const progress = asRecord(item.progress);
    return { id: item.id, parent_id: typeof item.parent_id === "string" ? item.parent_id : null,
        seq: Number(item.seq) || 0, kind: item.kind as ActivityItem["kind"],
        phase: ["work", "check", "final"].includes(String(item.phase)) ? item.phase as ActivityItem["phase"] : "work",
        label: item.label.slice(0, 160), purpose: typeof item.purpose === "string" ? item.purpose.slice(0, 500) : "",
        status: item.status as ActivityItem["status"], started_at: typeof item.started_at === "number" ? item.started_at : null,
        updated_at: Number(item.updated_at) || 0, attempt: Math.max(1, Number(item.attempt) || 1),
        finished_at: typeof item.finished_at === "number" ? item.finished_at : null,
        ...(typeof item.summary === "string" ? { summary: item.summary.slice(0, 1000) } : {}),
        ...(typeof item.tool === "string" ? { tool: item.tool.slice(0, 100) } : {}),
        ...(typeof progress.completed === "number" && Number.isFinite(progress.completed) && progress.completed >= 0
            ? { progress: { completed: progress.completed, ...(typeof progress.unit === "string" ? { unit: progress.unit } : {}),
                ...(typeof progress.total === "number" && progress.total > 0 ? { total: progress.total } : {}) } } : {}),
        outputs: (Array.isArray(item.outputs) ? item.outputs : []).flatMap((raw) => {
            const output = asRecord(raw);
            if (!["file", "source", "artifact", "ui"].includes(String(output.kind)) || typeof output.label !== "string") return [];
            return [{ kind: output.kind as ActivityItem["outputs"][number]["kind"], label: output.label.slice(0, 200),
                ...(typeof output.url === "string" ? { url: output.url } : {}),
                ...(typeof output.path === "string" ? { path: output.path } : {}),
                ...(typeof output.surface_id === "string" ? { surface_id: output.surface_id } : {}) }];
        }),
    };
}

const text = (value: unknown, max: number) => typeof value === "string" ? value.slice(0, max) : "";
const turnNumber = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
const SUBAGENT_KINDS = new Set(["started", "completed", "errored", "interrupted", "closed"]);

/** `subagent` events: public lifecycle and the child's final summary only. */
export function readSubagent(value: unknown): SubagentEvent | undefined {
    const event = asRecord(value);
    if (typeof event.id !== "string" || !event.id || !SUBAGENT_KINDS.has(String(event.kind))) return undefined;
    return { id: event.id.slice(0, 200), path: text(event.path, 300), nickname: text(event.nickname, 120), role: text(event.role, 120),
        kind: event.kind as SubagentEvent["kind"], status: text(event.status, 60) || String(event.kind), task: text(event.task, 500),
        ...(typeof event.summary === "string" ? { summary: event.summary.slice(0, 2000) } : {}) };
}

export function readApprovalRequest(value: unknown): ApprovalRequest | undefined {
    const event = asRecord(value);
    if (typeof event.id !== "string" || !event.id) return undefined;
    return { id: event.id.slice(0, 200), tool: text(event.tool, 120), title: text(event.title, 200) || text(event.tool, 120) || "Action",
        summary: text(event.summary, 1000), reason: text(event.reason, 1000),
        risk: ["low", "medium", "high", "critical"].includes(String(event.risk)) ? event.risk as ApprovalRequest["risk"] : "medium" };
}

export function readApprovalResolved(value: unknown): ApprovalResolution | undefined {
    const event = asRecord(value);
    if (typeof event.id !== "string" || !event.id || !["allow", "deny", "timeout"].includes(String(event.decision))
        || !["user", "guardian"].includes(String(event.by))) return undefined;
    return { id: event.id, decision: event.decision as ApprovalResolution["decision"], by: event.by as ApprovalResolution["by"] };
}

export function readGuardianReview(value: unknown): GuardianReview | undefined {
    const event = asRecord(value);
    if (typeof event.call_id !== "string" || !event.call_id || !["allow", "deny", "error"].includes(String(event.outcome))) return undefined;
    return { call_id: event.call_id.slice(0, 200), tool: text(event.tool, 120), outcome: event.outcome as GuardianReview["outcome"],
        risk_level: text(event.risk_level, 40), user_authorization: text(event.user_authorization, 40), rationale: text(event.rationale, 1000) };
}

export function readChangedFiles(value: unknown): ChangedFile[] {
    return (Array.isArray(value) ? value : []).slice(0, 500).flatMap((raw): ChangedFile[] => {
        const file = asRecord(raw);
        if (typeof file.path !== "string" || !file.path || !["A", "M", "D"].includes(String(file.status))) return [];
        const count = (v: unknown) => typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : undefined;
        const added = count(file.added), removed = count(file.removed);
        return [{ path: file.path.slice(0, 1000), status: file.status as ChangedFile["status"],
            ...(added !== undefined ? { added } : {}), ...(removed !== undefined ? { removed } : {}),
            ...(file.binary === true ? { binary: true } : {}), ...(file.untracked === true ? { untracked: true } : {}) }];
    });
}

/** `workspace_changes` events and saved `changes` summaries. */
export function readWorkspaceChanges(value: unknown): WorkspaceChanges | undefined {
    const event = asRecord(value);
    const turn = turnNumber(event.turn);
    if (turn === undefined) return undefined;
    return { turn, files: readChangedFiles(event.files) };
}

/** `mode` events and saved `content.mode`. */
export function readMode(value: unknown): AnswerMode | undefined {
    const event = asRecord(value);
    if (event.mode !== "mat" && event.mode !== "opt") return undefined;
    return { mode: event.mode, source: event.source === "auto" ? "auto" : "user", reason: text(event.reason, 500),
        ...(event.switched === true ? { switched: true } : {}),
        ...(event.from === "mat" || event.from === "opt" ? { from: event.from } : {}) };
}

function withTurn(state: StreamState, value: unknown): StreamState {
    const turn = turnNumber(value);
    return turn === undefined || state.turn === turn ? state : { ...state, turn };
}

function runningWork(): WorkSummary {
    return { status: "running", tool_count: 0, verification: { status: "not_requested", rounds: 0 }, steps: [], items: [] };
}

function terminalWork(work: WorkSummary | undefined, status: "complete" | "stopped" | "error"): WorkSummary | undefined {
    if (!work) return undefined;
    return { ...work, status, items: work.items?.map((item) => ["queued", "running", "paused"].includes(item.status)
        ? { ...item, status: status === "stopped" ? "cancelled" : status === "complete" && item.kind === "phase" ? "completed" : "failed" }
        : item) };
}

function findTool(parts: Part[], id: string) {
    return parts.find((p): p is Extract<Part, { type: "tool_call" }> => p.type === "tool_call" && p.id === id);
}

function isSurface(value: unknown): value is UISurface {
    const surface = asRecord(value);
    return surface.version === 1 && typeof surface.id === "string" && Boolean(surface.id)
        && Number.isSafeInteger(surface.revision) && Number(surface.revision) >= 1
        && (surface.kind === "native" || surface.kind === "mini_app")
        && ["preview", "committed", "error"].includes(String(surface.status))
        && typeof surface.title === "string" && typeof surface.fallback === "string"
        && Array.isArray(surface.nodes) && Array.isArray(surface.actions)
        && Boolean(surface.state) && typeof surface.state === "object" && !Array.isArray(surface.state);
}

function settlePreviews(parts: Part[]): Part[] {
    return parts.map((part) => part.type === "ui" && part.surface.status === "preview"
        ? { ...part, surface: { ...part.surface, status: "error", nodes: [], app: null, actions: [] } }
        : part);
}

function readablePart(part: Part): Part[] {
    if (part.type === "result_block") {
        const block = readResultBlock(part.block);
        return block ? [{ type: "result_block", block }] : [];
    }
    if (part.type !== "ui") return [part];
    const raw: unknown = part.surface;
    if (isSurface(raw)) return [part];
    const fallback = asRecord(raw).fallback;
    return typeof fallback === "string" ? [{ type: "text", text: fallback }] : [];
}

export function applyEvent(state: StreamState, event: StreamEvent): StreamState {
    if (typeof event.run_id === "string") {
        if (state.runId && state.runId !== event.run_id) return state;
        const seq = Number(event.seq);
        if (Number.isSafeInteger(seq) && seq > 0) {
            if (seq <= (state.lastSeq ?? 0)) return state;
            state = { ...state, runId: event.run_id, lastSeq: seq };
        } else state = { ...state, runId: event.run_id };
    }
    return reduceEvent(state, event);
}

function reduceEvent(state: StreamState, event: StreamEvent): StreamState {
    const type = event.type;
    if (type === "keepalive") return state;
    if (state.runId && ["reasoning_delta", "tool_call_start", "tool_call_delta", "tool_call_ready", "tool_progress", "tool_result", "verification", "phase", "plan", "monitor", "skills"].includes(type)) return state;

    if (type === "conversation") {
        return {
            ...state,
            conversationId: String(event.conversation_id ?? state.conversationId),
            title: typeof event.title === "string" ? event.title : state.title,
        };
    }

    if (type === "message_start") {
        return {
            ...state,
            messageId: String(event.message_id ?? state.messageId),
            provider: String(event.provider ?? state.provider),
            model: String(event.model ?? state.model),
            reasoning: String(event.reasoning ?? state.reasoning),
            parts: [],
            plan: [],
            usage: {},
            statusLine: null,
            status: "streaming",
            error: null,
            phase: "answer",
            presentation: undefined,
            work: undefined,
            earlierDrafts: undefined,
            notice: undefined,
            turn: turnNumber(event.turn),
            changes: undefined,
            guardianReviews: undefined,
            mode: undefined,
        };
    }

    if (type === "mode") {
        const mode = readMode(event);
        if (!mode) return state;
        if (mode.switched && !mode.from) {
            const from = state.mode && state.mode.mode !== mode.mode ? state.mode.mode : mode.mode === "opt" ? "mat" : "opt";
            return { ...state, mode: { ...mode, from } };
        }
        return { ...state, mode };
    }

    if (type === "subagent") {
        const agent = readSubagent(event);
        if (!agent) return state;
        const agents = state.subagents ?? [];
        return { ...state, subagents: agents.some((entry) => entry.id === agent.id)
            ? agents.map((entry) => entry.id === agent.id ? { ...agent, summary: agent.summary ?? entry.summary } : entry) : [...agents, agent] };
    }

    if (type === "approval_request") {
        const request = readApprovalRequest(event);
        if (!request || state.resolvedApprovals?.some((entry) => entry.id === request.id)) return state;
        const pending = state.approvals ?? [];
        return { ...state, approvals: pending.some((entry) => entry.id === request.id)
            ? pending.map((entry) => entry.id === request.id ? request : entry) : [...pending, request] };
    }

    if (type === "approval_resolved") {
        const resolved = readApprovalResolved(event);
        if (!resolved) return state;
        return { ...state, approvals: (state.approvals ?? []).filter((entry) => entry.id !== resolved.id),
            resolvedApprovals: [...(state.resolvedApprovals ?? []).filter((entry) => entry.id !== resolved.id), resolved].slice(-50) };
    }

    if (type === "guardian_review") {
        const review = readGuardianReview(event);
        if (!review) return state;
        const reviews = (state.guardianReviews ?? []).filter((entry) => entry.call_id !== review.call_id);
        return { ...state, guardianReviews: [...reviews, review].slice(-200) };
    }

    if (type === "workspace_changes") {
        const changes = readWorkspaceChanges(event);
        if (!changes) return state;
        return { ...state, turn: state.turn ?? changes.turn, changes };
    }

    if (type === "work") {
        const work = readWork(event.work);
        return work ? { ...state, work, statusLine: null } : state;
    }

    if (type === "activity") {
        const item = readActivity(event.item);
        if (!item) return state;
        const work = state.work ?? runningWork();
        const previous = work.items?.find((entry) => entry.id === item.id);
        if (previous && previous.seq > item.seq) return state;
        const items = previous ? work.items!.map((entry) => entry.id === item.id ? item : entry) : [...(work.items ?? []), item];
        return { ...state, work: { ...work, items, tool_count: items.filter((entry) => entry.kind === "tool").length } };
    }

    if (type === "progress") {
        const update = readProgress(event.update);
        const work = state.work ?? runningWork();
        return update ? { ...state, work: { ...work,
            current_progress: !work.current_progress || update.seq > work.current_progress.seq ? update : work.current_progress,
            milestones: readPublicMilestones([...(work.milestones ?? []), update]) } } : state;
    }

    if (type === "result_block") {
        if (state.status !== "streaming" || typeof event.message_id !== "string"
            || (state.messageId && state.messageId !== event.message_id)) return state;
        const block = readResultBlock(event.block);
        if (!block) return state;
        const previous = state.parts.find((part) => part.type === "result_block" && part.block.id === block.id);
        if (previous?.type === "result_block" && previous.block.revision >= block.revision) return state;
        const part: Part = { type: "result_block", block };
        return { ...state, messageId: event.message_id,
            parts: previous ? state.parts.map((entry) => entry === previous ? part : entry) : [...state.parts, part] };
    }

    if (type === "run_state") {
        if (!["queued", "running", "paused"].includes(String(event.status))) return state;
        const paused = event.status === "paused";
        return { ...state, paused, pauseRequested: paused ? false : state.pauseRequested,
            controlStatus: paused ? "Paused at a safe boundary" : state.paused ? "Resumed" : state.controlStatus,
            work: state.work ? { ...state.work, status: paused ? "paused" : "running" } : state.work };
    }

    if (type === "control_ack") {
        const queued = event.status === "queued";
        const action = String(event.action);
        const messages: Record<string, string> = {
            pause: queued ? "Pause requested; finishing the current action" : "Paused at a safe boundary",
            resume: queued ? "Resume requested" : "Resumed",
            steer: queued ? "Instruction queued" : "Instruction applied",
            stop: queued ? "Stopping…" : "Stopped",
        };
        if (!messages[action]) return state;
        return { ...state, controlStatus: messages[action],
            pauseRequested: action === "pause" ? queued : action === "resume" ? false : state.pauseRequested,
            paused: action === "resume" && !queued ? false : state.paused };
    }

    if (type === "final_start") {
        if (state.presentation === "final") return state;
        return { ...state, messageId: typeof event.message_id === "string" ? event.message_id : state.messageId,
            presentation: "final", parts: state.parts.filter((part) => part.type === "ui" || part.type === "artifact" || part.type === "result_block"), plan: [], phase: "answer", statusLine: null };
    }

    if (type === "status") {
        return { ...state, statusLine: "Working…" };
    }

    if (type === "artifact") {
        const artifact = asRecord(event.artifact);
        if (state.presentation !== "final" || typeof artifact.kind !== "string" || typeof artifact.name !== "string") return state;
        return { ...state, parts: [...state.parts, { type: "artifact", artifact: artifact as unknown as ToolResult["artifacts"][number] }] };
    }

    if (type === "ui_start" || type === "ui_patch" || type === "ui_commit") {
        if (!isSurface(event.surface)) return state;
        const surface = event.surface;
        const existing = state.parts.find((part) => part.type === "ui" && part.surface.id === surface.id);
        if (existing?.type === "ui") {
            if (surface.revision < existing.surface.revision
                || (existing.surface.status === "committed" && surface.status === "preview")) return state;
            return { ...state, parts: state.parts.map((part) => part === existing ? { type: "ui", surface } : part) };
        }
        return { ...state, parts: [...state.parts, { type: "ui", surface }] };
    }

    if (type === "ui_error") {
        const existing = state.parts.some((part) => part.type === "ui" && part.surface.id === event.surface_id);
        if (!existing && typeof event.fallback === "string" && event.fallback.trim()) {
            return { ...state, parts: [...state.parts, { type: "text", text: event.fallback, phase: state.phase }] };
        }
        return {
            ...state,
            parts: state.parts.map((part) => {
                if (part.type !== "ui" || part.surface.id !== event.surface_id || part.surface.status === "committed"
                    || Number(event.revision ?? 0) < part.surface.revision) return part;
                return { ...part, surface: { ...part.surface, status: "error", nodes: [], app: null, actions: [],
                    fallback: typeof event.fallback === "string" && event.fallback ? event.fallback : part.surface.fallback } };
            }),
        };
    }

    if (type === "skills") {
        const names = Array.isArray(event.names) ? event.names.map(String) : [];
        if (!names.length) return state;
        const parts = [...state.parts];
        const existing = parts.find((p) => p.type === "skills");
        if (existing && existing.type === "skills") {
            const merged = [...existing.names];
            for (const name of names) if (!merged.includes(name)) merged.push(name);
            return {
                ...state,
                parts: parts.map((p) => (p === existing ? { ...existing, names: merged, auto: Boolean(event.auto) && existing.auto } : p)),
            };
        }
        parts.unshift({ type: "skills", names, auto: Boolean(event.auto) });
        return { ...state, parts };
    }

    if (type === "options") {
        const items = Array.isArray(event.items) ? event.items.map(String).filter(Boolean) : [];
        if (!items.length) return state;
        const parts = [...state.parts.filter((p) => p.type !== "options"), { type: "options" as const, items }];
        return { ...state, parts };
    }

    if (type === "monitor") {
        return state;
    }

    if (type === "steer_applied") {
        return { ...state, controlStatus: "Instruction applied", statusLine: null };
    }

    if (type === "phase") {
        const phase = event.phase === "revise" ? "revise" : "verify";
        const round = Number(event.round ?? 1);
        return {
            ...state,
            phase,
            parts: [...state.parts, { type: "phase", phase, round }],
        };
    }

    if (type === "reasoning_delta") {
        // Older servers may still send this event. Never retain private reasoning.
        return event.delta ? { ...state, statusLine: null } : state;
    }

    if (type === "text_delta") {
        // Sequenced public runs require an explicit final-answer boundary.
        if (state.runId && state.presentation !== "final") return state;
        const delta = String(event.delta ?? "");
        const parts = [...state.parts];
        const last = parts[parts.length - 1];
        if (last?.type === "text") {
            parts[parts.length - 1] = { ...last, text: last.text + delta };
        } else {
            parts.push({ type: "text", text: delta, phase: state.phase });
        }
        return { ...state, parts, statusLine: delta ? null : state.statusLine };
    }

    if (type === "tool_call_start") {
        const id = String(event.id ?? "");
        const name = String(event.name ?? "");
        const parts = [...state.parts];
        const existing = findTool(parts, id);
        if (existing) {
            return {
                ...state,
                parts: parts.map((p) =>
                    p.type === "tool_call" && p.id === id ? { ...p, name: name || p.name } : p
                ),
            };
        }
        parts.push({
            type: "tool_call",
            id,
            name,
            arguments: {},
            result: null,
            status: "running",
            phase: state.phase,
            raw: "",
        });
        return { ...state, parts };
    }

    if (["tool_call_delta", "tool_call_ready", "tool_progress", "plan"].includes(type)) return state;

    if (type === "tool_result") {
        const id = String(event.id ?? "");
        const result: ToolResult = {
            ok: Boolean(event.ok),
            output: "",
            artifacts: Array.isArray(event.artifacts) ? (event.artifacts as ToolResult["artifacts"]) : [],
            error: null,
            meta: {},
        };
        return {
            ...state,
            parts: state.parts.map((p) =>
                p.type === "tool_call" && p.id === id
                    ? {
                          ...p,
                          name: String(event.name ?? p.name),
                          result,
                          status: result.ok ? ("completed" as const) : ("error" as const),
                      }
                    : p
            ),
        };
    }

    if (type === "verification") {
        return {
            ...state,
            parts: [
                ...state.parts,
                {
                    type: "verification",
                    verdict: String(event.verdict ?? "uncertain"),
                    report: "",
                    round: Number(event.round ?? 1),
                },
            ],
        };
    }

    if (type === "usage") {
        const next = { ...state.usage };
        for (const [key, value] of Object.entries(event)) {
            if (["type", "seq", "sequence", "created_at"].includes(key)) continue;
            if (typeof value === "number") next[key] = (next[key] ?? 0) + value;
        }
        return { ...state, usage: next };
    }

    if (type === "transcript") {
        const entry = event.entry as TranscriptEntry | undefined;
        if (!entry || typeof entry.id !== "string" || !Array.isArray(entry.order)) return state;
        return { ...state, transcript: upsertTranscript(state.transcript ?? [], entry) };
    }

    if (type === "message_end") {
        const content = event.content as AssistantContent | undefined;
        state = withTurn(withTurn(state, event.turn), content && typeof content === "object" ? (content as { turn?: unknown }).turn : undefined);
        if (content && Array.isArray(content.parts)) {
            const savedChanges = readWorkspaceChanges(content.changes);
            const savedMode = readMode(content.mode);
            return {
                ...state,
                ...(savedChanges && !state.changes ? { changes: savedChanges } : {}),
                // The live record keeps a mid-turn switch the saved summary may not carry.
                ...(savedMode && !(state.mode?.switched && savedMode.mode === state.mode.mode) ? { mode: savedMode } : {}),
                messageId: String(event.message_id ?? state.messageId),
                parts: settlePreviews(content.parts.filter((part) => part.type === "ui" || part.type === "artifact" || part.type === "options" || part.type === "result_block"
                    || (part.type === "text" && part.phase !== "verify")).flatMap(readablePart)),
                plan: [],
                usage: content.usage ?? state.usage,
                provider: content.provider || state.provider,
                model: content.model || state.model,
                reasoning: content.reasoning || state.reasoning,
                presentation: content.presentation,
                work: readWork(content.work) ?? state.work,
                transcript: Array.isArray(content.transcript) ? content.transcript : state.transcript,
                earlierDrafts: state.runId ? undefined : content.earlier_drafts?.filter((draft) => typeof draft === "string"),
                notice: typeof content.notice === "string" ? content.notice : undefined,
                status: content.status === "streaming" ? "complete" : content.status,
                statusLine: null,
            };
        }
        return {
            ...state,
            status: (event.status as StreamState["status"]) || state.status,
            statusLine: null,
        };
    }

    if (type === "done") {
        state = withTurn(state, event.turn);
        return {
            ...state,
            approvals: [],
            parts: settlePreviews(state.parts),
            conversationId: String(event.conversation_id ?? state.conversationId),
            messageId: String(event.message_id ?? state.messageId),
            status: state.status === "stopped" || state.status === "error" ? state.status : "complete",
            work: terminalWork(state.work, state.status === "stopped" || state.status === "error" ? state.status : "complete"),
            paused: false, pauseRequested: false, controlStatus: null,
            statusLine: null,
        };
    }

    if (type === "stopped") {
        return {
            ...state,
            conversationId: String(event.conversation_id ?? state.conversationId),
            messageId: String(event.message_id ?? state.messageId),
            status: "stopped",
            approvals: [],
            work: terminalWork(state.work, "stopped"),
            paused: false, pauseRequested: false, controlStatus: null,
            statusLine: null,
            parts: settlePreviews(state.parts).map((p) =>
                p.type === "tool_call" && p.status === "running" ? { ...p, status: "stopped" } : p
            ),
        };
    }

    if (type === "error") {
        return {
            ...state,
            parts: settlePreviews(state.parts),
            status: "error",
            approvals: [],
            work: terminalWork(state.work, "error"),
            paused: false, pauseRequested: false, controlStatus: null,
            error: String(event.message ?? "The run failed"),
            statusLine: null,
        };
    }

    return state;
}

function parseDetail(body: unknown): string {
    if (!body || typeof body !== "object") return "Chat request failed";
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
        return detail.map((item) => (item && typeof item === "object" && "msg" in item ? String(item.msg) : JSON.stringify(item))).join("; ");
    }
    return "Chat request failed";
}

export async function streamChat(
    body: ChatStreamBody,
    onEvent: (event: StreamEvent) => void,
    signal?: AbortSignal
): Promise<"done" | "stopped" | "error"> {
    const res = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify(body),
        signal,
    });
    return consumeEventStream(res, onEvent, signal);
}

export async function streamRun(runId: string, after: number, onEvent: (event: StreamEvent) => void, signal?: AbortSignal) {
    const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/events?after=${after}`, { headers: { Accept: "text/event-stream" }, signal });
    return consumeEventStream(res, onEvent, signal);
}

async function consumeEventStream(res: Response, onEvent: (event: StreamEvent) => void, signal?: AbortSignal): Promise<"done" | "stopped" | "error"> {
    if (!res.ok) {
        let message = `HTTP ${res.status}`;
        try {
            message = parseDetail(await res.json());
        } catch {
            message = await res.text();
        }
        throw Object.assign(new Error(message), { status: res.status });
    }
    if (!res.body) throw new Error("No response body");

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let terminal: "done" | "stopped" | "error" | null = null;

    const consumeFrame = (frame: string) => {
        const dataLines = frame
            .split("\n")
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart());
        if (!dataLines.length) return;
        const payload = dataLines.join("\n");
        let event: StreamEvent;
        try {
            event = JSON.parse(payload) as StreamEvent;
        } catch {
            return;
        }
        if (event.type === "keepalive") return;
        onEvent(event);
        if (event.type === "done" || event.type === "stopped" || event.type === "error") {
            terminal = event.type;
        }
    };

    const cancel = () => { void reader.cancel().catch(() => {}); };
    signal?.addEventListener("abort", cancel, { once: true });
    try {
    while (true) {
        if (signal?.aborted) throw new DOMException("Subscription closed", "AbortError");
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        let separator = /\r?\n\r?\n/.exec(buffer);
        while (separator) {
            consumeFrame(buffer.slice(0, separator.index).replace(/\r/g, ""));
            buffer = buffer.slice(separator.index + separator[0].length);
            separator = /\r?\n\r?\n/.exec(buffer);
        }
        if (terminal) { await reader.cancel().catch(() => {}); return terminal; }
        if (done) break;
    }
    if (buffer.trim()) consumeFrame(buffer.replace(/\r/g, ""));
    if (signal?.aborted) throw new DOMException("Subscription closed", "AbortError");
    if (!terminal) throw new Error("The connection ended before the response finished. Reload the chat to recover saved results.");
    return terminal;
    } finally { signal?.removeEventListener("abort", cancel); reader.releaseLock(); }
}

export function upsertTranscript(entries: TranscriptEntry[], entry: TranscriptEntry): TranscriptEntry[] {
    const next = entries.some((existing) => existing.id === entry.id)
        ? entries.map((existing) => existing.id === entry.id ? entry : existing)
        : [...entries, entry];
    return next.sort((a, b) => a.order[0] - b.order[0] || a.order[1] - b.order[1]);
}
