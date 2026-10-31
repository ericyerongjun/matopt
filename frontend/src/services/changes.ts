/** Per-turn workspace changes: GET /api/conversations/{id}/turns/{turn}/diff. */
import apiClient, { isUnavailable } from "./api.ts";
import { readChangedFiles } from "./stream.ts";
import type { ChangedFile } from "../types/protocol.ts";

export interface TurnDiff {
    turn: number;
    files: ChangedFile[];
    /** Unified diff text for all files. */
    diff: string;
    truncated: boolean;
}

export function readTurnDiff(value: unknown, turn: number): TurnDiff {
    const data = value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
    return { turn: typeof data.turn === "number" ? data.turn : turn, files: readChangedFiles(data.files),
        diff: typeof data.diff === "string" ? data.diff : "", truncated: data.truncated === true };
}

export async function getTurnDiff(conversationId: string, turn: number, signal?: AbortSignal): Promise<TurnDiff> {
    const { data } = await apiClient.get(`/conversations/${encodeURIComponent(conversationId)}/turns/${turn}/diff`, { signal });
    return readTurnDiff(data, turn);
}

/** Saved answers know their turn but not their files; summaries are fetched once per turn. */
const summaries = new Map<string, Promise<ChangedFile[]>>();
export function turnChangeSummary(conversationId: string, turn: number): Promise<ChangedFile[]> {
    const key = `${conversationId}:${turn}`;
    let pending = summaries.get(key);
    if (!pending) {
        pending = getTurnDiff(conversationId, turn).then((diff) => diff.files, (error) => {
            // Unavailable means "no changes to show"; other failures may be retried later.
            if (!isUnavailable(error)) summaries.delete(key);
            return [];
        });
        summaries.set(key, pending);
    }
    return pending;
}
export function clearTurnChangeCache() { summaries.clear(); }

/** Splits a multi-file unified diff into one patch per file path. */
export function splitUnifiedDiff(diff: string): Map<string, string> {
    const patches = new Map<string, string>();
    if (!diff.trim()) return patches;
    const lines = diff.split("\n");
    let start = -1;
    const flush = (end: number) => {
        if (start < 0) return;
        const chunk = lines.slice(start, end);
        const path = patchPath(chunk);
        if (path) patches.set(path, (patches.has(path) ? patches.get(path)! + "\n" : "") + chunk.join("\n").replace(/\n+$/, ""));
    };
    const isGit = lines.some((line) => line.startsWith("diff --git "));
    for (let index = 0; index < lines.length; index++) {
        const line = lines[index];
        const begins = isGit ? line.startsWith("diff --git ")
            : line.startsWith("--- ") && lines[index + 1]?.startsWith("+++ ");
        if (begins) { flush(index); start = index; }
    }
    flush(lines.length);
    return patches;
}

function stripPrefix(value: string) {
    const path = value.trim().replace(/\t.*$/, "").replace(/^"(.*)"$/, "$1");
    return path === "/dev/null" ? "" : path.replace(/^[ab]\//, "");
}

function patchPath(chunk: string[]): string {
    const next = chunk.find((line) => line.startsWith("+++ "));
    const previous = chunk.find((line) => line.startsWith("--- "));
    const fromMarkers = (next && stripPrefix(next.slice(4))) || (previous && stripPrefix(previous.slice(4)));
    if (fromMarkers) return fromMarkers;
    const header = chunk[0]?.match(/^diff --git a\/(.+?) b\/(.+)$/);
    return header ? header[2] : "";
}
