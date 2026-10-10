import type { ChatMessage } from "../types/protocol.ts";
import { isAssistantContent } from "../types/protocol.ts";

/** The latest answer block in the latest assistant message, including revisions.
 * A turn without an answer stays empty instead of showing a previous turn's result. */
export function latestResultText(messages: ChatMessage[]): string {
    let latest: ChatMessage | undefined;
    for (let index = messages.length - 1; index >= 0; index--) {
        if (messages[index].role === "assistant") { latest = messages[index]; break; }
    }
    if (!latest || !isAssistantContent(latest.content)) return "";
    for (let index = latest.content.parts.length - 1; index >= 0; index--) {
        const part = latest.content.parts[index];
        if (part.type === "text" && part.phase !== "verify" && part.text.trim()) return part.text;
        if (part.type === "ui" && part.surface.status === "committed" && part.surface.fallback.trim()) return part.surface.fallback;
    }
    return "";
}
