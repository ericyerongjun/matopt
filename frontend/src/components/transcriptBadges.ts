import type { TranscriptEntry } from "../types/protocol.ts";

export interface ShellBadge { label: string; tone: "active" | "success" | "danger" | "neutral" }

/**
 * Background shell sessions announce themselves in the exec header
 * ("Process running with session ID N" … "Output:"). Only the header is read.
 */
export function shellSessionBadge(entry: Pick<TranscriptEntry, "tool" | "output">, live = true): ShellBadge | null {
    if (typeof entry.output !== "string" || !entry.tool || !/^(bash|bash_poll|shell)$/.test(entry.tool)) return null;
    const outputAt = entry.output.search(/^Output:/m);
    const header = entry.output.slice(0, outputAt >= 0 ? outputAt : 600);
    const running = /Process running with session ID\s+\S+/.test(header);
    const exit = /^Exit code:\s*(-?\d+)\s*$/m.exec(header);
    if (running && !exit) return live ? { label: "Background · running", tone: "active" } : { label: "Background", tone: "neutral" };
    // A poll that reports the end of a background session.
    if (exit && (entry.tool === "bash_poll" || running)) return { label: `exit ${exit[1]}`, tone: exit[1] === "0" ? "success" : "danger" };
    return null;
}
