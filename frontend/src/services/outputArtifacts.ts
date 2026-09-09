import type { Artifact, ChatMessage, Part } from "../types/protocol.ts";
import { isAssistantContent } from "../types/protocol.ts";

const OUTPUT_KINDS = new Set(["image", "plotly", "html", "html_file"]);

type ToolPart = Extract<Part, { type: "tool_call" }>;

function latestAssistant(messages: ChatMessage[]): ChatMessage | undefined {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
        const msg = messages[i];
        if (msg.role === "assistant" && isAssistantContent(msg.content)) return msg;
    }
    return undefined;
}

function artifactKey(artifact: Artifact): string | null {
    if (artifact.digest) return `d:${artifact.digest}`;
    if (artifact.path) return `p:${artifact.path}`;
    return null;
}

/**
 * Figures for the Work pane Output section.
 *
 * The agent (and the verify pass) often re-runs a plotting cell. The kernel
 * now skips identical bytes, but restyled copies and leftover recaptures still
 * used to stack here because the pane flattened every tool call in the thread.
 * Keep the latest assistant turn, drop verify/revise recaptures, and collapse
 * identical digest/path (last write wins).
 */
export function selectOutputArtifacts(messages: ChatMessage[]): Artifact[] {
    const latest = latestAssistant(messages);
    if (!latest || !isAssistantContent(latest.content)) return [];

    const collected: Artifact[] = [];
    for (const part of latest.content.parts) {
        if (part.type !== "tool_call") continue;
        const tool = part as ToolPart;
        if (tool.phase === "verify" || tool.phase === "revise") continue;
        for (const art of tool.result?.artifacts ?? []) {
            if (OUTPUT_KINDS.has(String(art.kind))) collected.push(art);
        }
    }

    const seen = new Set<string>();
    const unique: Artifact[] = [];
    for (let i = collected.length - 1; i >= 0; i -= 1) {
        const art = collected[i];
        const key = artifactKey(art);
        if (key) {
            if (seen.has(key)) continue;
            seen.add(key);
        }
        unique.push(art);
    }
    return unique.reverse();
}
