/** Types that mirror docs/PROTOCOL.md (REST + SSE). */
import type { UISurface } from "./ui";

export type ProviderId = string;
export type ReasoningLevel = "off" | "low" | "medium" | "high" | "max";
export type Phase = "answer" | "verify" | "revise";
export type MessageStatus = "complete" | "stopped" | "error" | "streaming";

export interface ReasoningStop {
    id: ReasoningLevel;
    label: string;
}

export interface ProviderModel {
    id: string;
    label: string;
    family: string;
    description: string;
    context_window: number;
    max_output: number;
    reasoning_levels: ReasoningLevel[];
    default_reasoning: ReasoningLevel;
    tags: string[];
}

export interface ProviderInfo {
    id: ProviderId;
    label: string;
    configured: boolean;
    env_key: string;
    docs_url: string;
    default_model: string;
    models: ProviderModel[];
}

export interface ProviderCatalog {
    default_provider: ProviderId | null;
    default_model: string | null;
    default_reasoning: ReasoningLevel;
    verify_available: boolean;
    levels: ReasoningStop[];
    providers: ProviderInfo[];
}

export interface Conversation {
    id: string;
    title: string;
    provider: string | null;
    model: string | null;
    reasoning: string | null;
    created_at: number;
    updated_at: number;
    meta: Record<string, unknown>;
    message_count?: number;
    running?: boolean;
}

export interface Attachment {
    name: string;
    path: string;
    url?: string;
    size?: number;
    kind?: string;
}

export interface UserContent {
    text: string;
    attachments: Attachment[];
}

export interface Artifact {
    kind: "image" | "plotly" | "html" | "file" | "markdown" | "json" | "html_file" | string;
    name: string;
    path?: string;
    url?: string;
    mime?: string;
    size?: number;
    data?: unknown;
    digest?: string;
}

export interface ToolResult {
    ok: boolean;
    output: string;
    artifacts: Artifact[];
    error: string | null;
    meta: Record<string, unknown>;
}

export interface PlanStep {
    content: string;
    status: "pending" | "in_progress" | "completed";
}

export interface Usage {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    reasoning_tokens?: number;
    cached_tokens?: number;
    [key: string]: number | undefined;
}

export type Part =
    | { type: "text"; text: string; phase?: Phase }
    | { type: "result_block"; block: ResultBlock }
    | { type: "ui"; surface: UISurface }
    | { type: "artifact"; artifact: Artifact }
    | { type: "reasoning"; text: string; phase?: Phase }
    | {
          type: "tool_call";
          id: string;
          name: string;
          arguments: Record<string, unknown>;
          result: ToolResult | null;
          status: "running" | "completed" | "error" | "stopped";
          phase?: Phase;
          /** Live JSON argument buffer while the model is still emitting. */
          raw?: string;
      }
    | { type: "phase"; phase: "verify" | "revise"; round: number }
    | { type: "verification"; verdict: "correct" | "incorrect" | "uncertain" | string; report: string; round: number }
    | { type: "skills"; names: string[]; auto?: boolean }
    | { type: "options"; items: string[] }
    | { type: "monitor"; level: string; summary: string; tool?: string };

/** Explicit public events. These never contain model reasoning, tool arguments or stdout. */
export type ActivityStatus = "queued" | "running" | "completed" | "failed" | "cancelled" | "paused";
export type ActivityPhase = "work" | "check" | "final";
export interface ActivityOutput {
    kind: "file" | "source" | "artifact" | "ui";
    label: string;
    url?: string;
    path?: string;
    surface_id?: string;
}
export interface ActivityItem {
    id: string;
    parent_id: string | null;
    seq: number;
    kind: "phase" | "tool" | "agent" | "control";
    phase: ActivityPhase;
    label: string;
    purpose: string;
    status: ActivityStatus;
    started_at: number | null;
    updated_at: number;
    finished_at: number | null;
    summary?: string;
    tool?: string;
    attempt: number;
    progress?: { completed: number; total?: number; unit?: string };
    outputs: ActivityOutput[];
}
export interface ProgressUpdate {
    id: string;
    seq: number;
    text: string;
    created_at: number;
    phase: ActivityPhase;
    item_id?: string;
}

/** Deliberately published result content; never inferred from internal model text. */
export interface ResultBlock {
    id: string;
    revision: number;
    kind: "markdown" | "source";
    status: "provisional" | "accepted" | "superseded" | "withdrawn";
    content: string;
    evidence_refs: string[];
    created_at: number;
}

export function readResultBlock(value: unknown): ResultBlock | undefined {
    if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
    const block = value as Record<string, unknown>;
    if (typeof block.id !== "string" || !block.id || block.id.length > 160
        || !Number.isSafeInteger(block.revision) || Number(block.revision) < 1
        || !["markdown", "source"].includes(String(block.kind))
        || !["provisional", "accepted", "superseded", "withdrawn"].includes(String(block.status))
        || typeof block.content !== "string" || block.content.length > 50_000
        || typeof block.created_at !== "number" || !Number.isFinite(block.created_at) || block.created_at < 0
        || !Array.isArray(block.evidence_refs) || block.evidence_refs.length > 50
        || !block.evidence_refs.every((ref) => typeof ref === "string" && ref.length <= 500)) return undefined;
    return { id: block.id, revision: Number(block.revision), kind: block.kind as ResultBlock["kind"],
        status: block.status as ResultBlock["status"], content: block.content,
        evidence_refs: [...block.evidence_refs] as string[], created_at: block.created_at };
}

export function readPublicProgress(value: unknown): ProgressUpdate | undefined {
    if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
    const update = value as Record<string, unknown>;
    if (typeof update.id !== "string" || !update.id || typeof update.text !== "string"
        || !["work", "check", "final"].includes(String(update.phase))) return undefined;
    return { id: update.id, text: update.text.slice(0, 1000), seq: typeof update.seq === "number" && Number.isSafeInteger(update.seq) && update.seq >= 0 ? update.seq : 0,
        created_at: typeof update.created_at === "number" && Number.isFinite(update.created_at) && update.created_at >= 0 ? update.created_at : 0,
        phase: update.phase as ProgressUpdate["phase"], ...(typeof update.item_id === "string" ? { item_id: update.item_id } : {}) };
}

export function readPublicMilestones(value: unknown): ProgressUpdate[] {
    const updates = new Map<string, ProgressUpdate>();
    for (const raw of Array.isArray(value) ? value : []) {
        const update = readPublicProgress(raw);
        if (update && (!updates.has(update.id) || updates.get(update.id)!.seq < update.seq)) updates.set(update.id, update);
    }
    return [...updates.values()].sort((a, b) => a.seq - b.seq).slice(-12);
}

/** Public progress contains safe summaries, never model reasoning or tool payloads. */
export interface WorkSummary {
    status: "running" | "paused" | "complete" | "stopped" | "error";
    tool_count: number;
    verification: {
        status: "not_requested" | "skipped" | "correct" | "incorrect" | "uncertain" | "limit_reached" | "error";
        rounds: number;
    };
    steps: Array<{ label: string; status: "running" | "complete" | "error" }>;
    duration_ms?: number;
    items?: ActivityItem[];
    current_progress?: ProgressUpdate | null;
    milestones?: ProgressUpdate[];
    started_at?: number;
    finished_at?: number;
}

export interface AssistantContent {
    parts: Part[];
    model: string;
    provider: string;
    reasoning: string;
    usage: Usage;
    status: MessageStatus;
    plan: PlanStep[];
    stats: { llm_calls: number; tool_calls: number };
    presentation?: "final";
    work?: WorkSummary;
    /** Explicitly selected prior drafts for older conversations with partial corrections. */
    earlier_drafts?: string[];
    /** Controlled explanation when an older result cannot be safely consolidated. */
    notice?: string;
}

export interface ChatMessage {
    id: string;
    conversation_id: string;
    seq: number;
    role: "user" | "assistant";
    content: UserContent | AssistantContent;
    created_at: number;
}

export interface WorkspaceFile {
    path: string;
    size: number;
    mtime: number;
    url: string;
    kind: string;
    name: string;
}

export interface UploadedFile extends Attachment {
    card?: string | null;
}

export interface StreamEvent {
    type: string;
    [key: string]: unknown;
}

export function isUserContent(c: ChatMessage["content"]): c is UserContent {
    return !("parts" in c);
}

export function isAssistantContent(c: ChatMessage["content"]): c is AssistantContent {
    return Array.isArray((c as AssistantContent).parts);
}

export function textFromAssistant(content: AssistantContent): string {
    return content.parts
        .flatMap((p) => p.type === "text" && p.phase !== "verify" ? [p.text] : p.type === "ui" ? [p.surface.fallback] : [])
        .join("\n\n")
        .trim();
}
