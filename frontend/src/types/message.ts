/** Roles for chat messages. */
export type MessageRole = "user" | "assistant" | "system";

/** A single chat message. */
export interface Message {
    id: string;
    role: MessageRole;
    content: string;
    timestamp: number;
    /** Tool calls made during this message (assistant only). */
    toolCalls?: ToolCall[];
}

export interface ToolCall {
    name: string;
    arguments: Record<string, unknown>;
    result?: string;
}
