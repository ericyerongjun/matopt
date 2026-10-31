/** Import MCP servers, skills and instruction files from Claude Code, Cursor or Codex. */
import apiClient from "./api.ts";

export interface MigrationItem {
    id: string;
    source: string;
    scope: string;
    item_type: string;
    path: string;
    summary: string;
    count: number;
    conflicts: string[];
}
export interface MigrationResult { id: string; status: string; detail: string }

const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const str = (value: unknown) => typeof value === "string" ? value : "";

export function readMigrationItem(value: unknown): MigrationItem | null {
    const item = record(value);
    if (!str(item.id)) return null;
    const conflicts = Array.isArray(item.conflicts) ? item.conflicts.map((entry) => typeof entry === "string" ? entry : JSON.stringify(entry))
        : typeof item.conflicts === "number" && item.conflicts > 0 ? [`${item.conflicts} existing ${item.conflicts === 1 ? "entry" : "entries"}`] : [];
    return { id: str(item.id), source: str(item.source), scope: str(item.scope), item_type: str(item.item_type), path: str(item.path),
        summary: str(item.summary), count: typeof item.count === "number" ? item.count : 0, conflicts };
}

export const migrationService = {
    async detect(signal?: AbortSignal): Promise<MigrationItem[]> {
        const data = record((await apiClient.get("/migration/detect", { signal })).data);
        return (Array.isArray(data.items) ? data.items : []).map(readMigrationItem).filter((item): item is MigrationItem => Boolean(item));
    },
    async import(ids: string[]): Promise<MigrationResult[]> {
        const data = record((await apiClient.post("/migration/import", { items: ids })).data);
        return (Array.isArray(data.results) ? data.results : []).flatMap((raw) => {
            const result = record(raw);
            return str(result.id) ? [{ id: str(result.id), status: str(result.status) || "unknown", detail: str(result.detail) }] : [];
        });
    },
};
