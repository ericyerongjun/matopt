/** Types that mirror docs/PROTOCOL.md (REST + SSE). */

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

export interface AssistantContent {
    parts: Part[];
    model: string;
    provider: string;
    reasoning: string;
    usage: Usage;
    status: MessageStatus;
    plan: PlanStep[];
    stats: { llm_calls: number; tool_calls: number };
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
        .filter((p): p is Extract<Part, { type: "text" }> => p.type === "text")
        .map((p) => p.text)
        .join("\n\n")
        .trim();
}
