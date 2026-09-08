/**
 * SSE client + reducer for POST /api/chat/stream.
 *
 * Frames are `data: <json>\n\n`. Keepalive is ignored. `done` / `stopped` /
 * `error` end the stream. The reducer builds ordered message parts the same
 * way PROTOCOL.md describes, so a mid-stream UI and a persisted message share
 * one renderer.
 */

import type {
    AssistantContent,
    Attachment,
    Part,
    PlanStep,
    StreamEvent,
    ToolResult,
    Usage,
} from "../types/protocol";

export interface ChatStreamBody {
    conversation_id?: string;
    message: string;
    attachments?: Attachment[];
    provider?: string | null;
    model?: string | null;
    reasoning?: string | null;
    verify?: boolean;
    temperature?: number;
}

export interface StreamState {
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
        stats: { llm_calls: 0, tool_calls: state.parts.filter((p) => p.type === "tool_call").length },
    };
}

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : {};
}

function findTool(parts: Part[], id: string) {
    return parts.find((p): p is Extract<Part, { type: "tool_call" }> => p.type === "tool_call" && p.id === id);
}

function tryParseArgs(raw: string): Record<string, unknown> | null {
    try {
        const v = JSON.parse(raw);
        if (v && typeof v === "object" && !Array.isArray(v)) return v as Record<string, unknown>;
    } catch {
        /* incomplete */
    }
    return null;
}

export function applyEvent(state: StreamState, event: StreamEvent): StreamState {
    const type = event.type;
    if (type === "keepalive") return state;

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
        };
    }

    if (type === "status") {
        return { ...state, statusLine: String(event.message ?? "") };
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
        const delta = String(event.delta ?? "");
        const parts = [...state.parts];
        const last = parts[parts.length - 1];
        if (last?.type === "reasoning") {
            parts[parts.length - 1] = { ...last, text: last.text + delta };
        } else {
            parts.push({ type: "reasoning", text: delta, phase: state.phase });
        }
        return { ...state, parts };
    }

    if (type === "text_delta") {
        const delta = String(event.delta ?? "");
        const parts = [...state.parts];
        const last = parts[parts.length - 1];
        if (last?.type === "text") {
            parts[parts.length - 1] = { ...last, text: last.text + delta };
        } else {
            parts.push({ type: "text", text: delta, phase: state.phase });
        }
        return { ...state, parts };
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

    if (type === "tool_call_delta") {
        const id = String(event.id ?? "");
        const delta = String(event.delta ?? "");
        return {
            ...state,
            parts: state.parts.map((p) => {
                if (p.type !== "tool_call" || p.id !== id) return p;
                const raw = (p.raw || "") + delta;
                const parsed = tryParseArgs(raw);
                return {
                    ...p,
                    raw,
                    arguments: parsed ?? { _raw: raw },
                };
            }),
        };
    }

    if (type === "tool_call_ready") {
        const id = String(event.id ?? "");
        const name = String(event.name ?? "");
        const args = asRecord(event.arguments);
        return {
            ...state,
            parts: state.parts.map((p) =>
                p.type === "tool_call" && p.id === id
                    ? { ...p, name: name || p.name, arguments: args, status: "running" as const }
                    : p
            ),
        };
    }

    if (type === "tool_progress") {
        const id = String(event.id ?? "");
        const delta = String(event.delta ?? "");
        return {
            ...state,
            parts: state.parts.map((p) => {
                if (p.type !== "tool_call" || p.id !== id) return p;
                const prev = p.result ?? { ok: true, output: "", artifacts: [], error: null, meta: {} };
                return { ...p, result: { ...prev, output: prev.output + delta } };
            }),
        };
    }

    if (type === "plan") {
        const steps = Array.isArray(event.steps) ? (event.steps as PlanStep[]) : state.plan;
        return { ...state, plan: steps };
    }

    if (type === "tool_result") {
        const id = String(event.id ?? "");
        const result: ToolResult = {
            ok: Boolean(event.ok),
            output: String(event.output ?? ""),
            artifacts: Array.isArray(event.artifacts) ? (event.artifacts as ToolResult["artifacts"]) : [],
            error: (event.error as string | null) ?? null,
            meta: asRecord(event.meta),
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
                    report: String(event.report ?? ""),
                    round: Number(event.round ?? 1),
                },
            ],
        };
    }

    if (type === "usage") {
        const next = { ...state.usage };
        for (const [key, value] of Object.entries(event)) {
            if (key === "type") continue;
            if (typeof value === "number") next[key] = (next[key] ?? 0) + value;
        }
        return { ...state, usage: next };
    }

    if (type === "message_end") {
        const content = event.content as AssistantContent | undefined;
        if (content && Array.isArray(content.parts)) {
            return {
                ...state,
                messageId: String(event.message_id ?? state.messageId),
                parts: content.parts,
                plan: content.plan ?? state.plan,
                usage: content.usage ?? state.usage,
                provider: content.provider || state.provider,
                model: content.model || state.model,
                reasoning: content.reasoning || state.reasoning,
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
        return {
            ...state,
            conversationId: String(event.conversation_id ?? state.conversationId),
            messageId: String(event.message_id ?? state.messageId),
            status: "complete",
            statusLine: null,
        };
    }

    if (type === "stopped") {
        return {
            ...state,
            conversationId: String(event.conversation_id ?? state.conversationId),
            messageId: String(event.message_id ?? state.messageId),
            status: "stopped",
            statusLine: null,
            parts: state.parts.map((p) =>
                p.type === "tool_call" && p.status === "running" ? { ...p, status: "stopped" } : p
            ),
        };
    }

    if (type === "error") {
        return {
            ...state,
            status: "error",
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
    if (!res.ok) {
        let message = `HTTP ${res.status}`;
        try {
            message = parseDetail(await res.json());
        } catch {
            message = await res.text();
        }
        throw new Error(message);
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

    while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        let idx = buffer.indexOf("\n\n");
        while (idx !== -1) {
            consumeFrame(buffer.slice(0, idx).replace(/\r/g, ""));
            buffer = buffer.slice(idx + 2);
            idx = buffer.indexOf("\n\n");
        }
        if (done) break;
    }
    if (buffer.trim()) consumeFrame(buffer.replace(/\r/g, ""));
    return terminal ?? "done";
}
