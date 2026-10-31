/** Local plugins bundling skills, MCP servers and hooks. */
import apiClient from "./api.ts";

export interface PluginInfo {
    id: string;
    name: string;
    version: string;
    description: string;
    enabled: boolean;
    skills: string[];
    mcp_servers: string[];
    hooks: string[];
    errors: string[];
}
export interface PluginMutation { plugin: PluginInfo | null; restart_required: boolean }

const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const str = (value: unknown) => typeof value === "string" ? value : "";
/** Names may arrive as strings or as `{name}` objects. */
const names = (value: unknown) => (Array.isArray(value) ? value : []).flatMap((item) => typeof item === "string" ? [item] : str(record(item).name) ? [str(record(item).name)] : []);

export function readPlugin(value: unknown): PluginInfo | null {
    const plugin = record(value);
    if (!str(plugin.id)) return null;
    return { id: str(plugin.id), name: str(plugin.name) || str(plugin.id), version: str(plugin.version), description: str(plugin.description),
        enabled: plugin.enabled === true, skills: names(plugin.skills), mcp_servers: names(plugin.mcp_servers), hooks: names(plugin.hooks),
        errors: (Array.isArray(plugin.errors) ? plugin.errors : []).map((error) => typeof error === "string" ? error : JSON.stringify(error)) };
}

function mutation(value: unknown): PluginMutation {
    const data = record(value);
    return { plugin: readPlugin(data.plugin ?? data), restart_required: data.restart_required === true };
}

export const pluginsService = {
    async list(signal?: AbortSignal): Promise<PluginInfo[]> {
        const data = record((await apiClient.get("/plugins", { signal })).data);
        return (Array.isArray(data.plugins) ? data.plugins : []).map(readPlugin).filter((plugin): plugin is PluginInfo => Boolean(plugin));
    },
    async install(path: string) { return mutation((await apiClient.post("/plugins/install", { path })).data); },
    async setEnabled(id: string, enabled: boolean) {
        return mutation((await apiClient.post(`/plugins/${encodeURIComponent(id)}/${enabled ? "enable" : "disable"}`)).data);
    },
    async remove(id: string) { return mutation((await apiClient.delete(`/plugins/${encodeURIComponent(id)}`)).data); },
};
