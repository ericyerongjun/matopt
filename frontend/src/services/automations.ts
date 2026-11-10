/** Scheduled and watched agent turns created by /loop and /automate. */
import apiClient from "./api.ts";

export interface Automation {
    id: string; name: string; prompt: string; conversation_id: string; schedule_text: string;
    watch: { path: string; pattern: string | null } | null; enabled: boolean; status: string;
    next_run_at: number | null; last_run_at: number | null; run_count: number; last_status: string | null;
}

const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const str = (value: unknown) => typeof value === "string" ? value : "";
const num = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : null;

export function readAutomation(value: unknown): Automation | null {
    const a = record(value);
    if (!str(a.id)) return null;
    const watch = record(a.watch);
    return { id: str(a.id), name: str(a.name) || str(a.id), prompt: str(a.prompt), conversation_id: str(a.conversation_id),
        schedule_text: str(a.schedule_text), watch: str(watch.path) ? { path: str(watch.path), pattern: str(watch.pattern) || null } : null,
        enabled: a.enabled === true, status: str(a.status) || "active", next_run_at: num(a.next_run_at), last_run_at: num(a.last_run_at),
        run_count: num(a.run_count) ?? 0, last_status: str(a.last_status) || null };
}

export const automationsService = {
    async list(signal?: AbortSignal): Promise<Automation[]> {
        const data = record((await apiClient.get("/automations", { signal })).data);
        return (Array.isArray(data.automations) ? data.automations : []).map(readAutomation).filter((a): a is Automation => Boolean(a));
    },
    async setEnabled(id: string, enabled: boolean) { return readAutomation((await apiClient.patch(`/automations/${encodeURIComponent(id)}`, { enabled })).data); },
    async runNow(id: string) { return readAutomation((await apiClient.post(`/automations/${encodeURIComponent(id)}/run`)).data); },
    async remove(id: string) { await apiClient.delete(`/automations/${encodeURIComponent(id)}`); },
};
