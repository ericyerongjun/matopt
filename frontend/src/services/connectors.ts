/** One-click MCP connectors (well-known servers) and configs for using MatOpt from other MCP clients. */
import apiClient from "./api.ts";

export interface ConnectorInput { key: string; label: string; kind: "path" | "text" | "secret"; required: boolean; env?: string; default?: string; help?: string }
export interface Connector {
    id: string; name: string; vendor: string; category: string; description: string; docs: string;
    auth: "none" | "token" | "optional-token"; inputs: ConnectorInput[]; requires: string[]; notes: string; tools: string[];
    transport: "remote" | "local"; installed: boolean; available: boolean; missing: string[]; secrets: Record<string, boolean>;
    enabled?: boolean; state?: "connected" | "failed" | "disabled" | "pending"; error?: string; tool_count?: number;
}
export interface ClientInfo { id: string; name: string; path: string | null; note: string }
export interface ClientConfig { client: string; name: string; snippet: string; path: string | null; note: string; command: string }

const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const str = (value: unknown) => typeof value === "string" ? value : "";
const strings = (value: unknown) => (Array.isArray(value) ? value : []).filter((item): item is string => typeof item === "string");
const STATES = new Set(["connected", "failed", "disabled", "pending"]);

function readInput(value: unknown): ConnectorInput | null {
    const input = record(value);
    const kind = str(input.kind);
    if (!str(input.key) || !["path", "text", "secret"].includes(kind)) return null;
    return { key: str(input.key), label: str(input.label) || str(input.key), kind: kind as ConnectorInput["kind"], required: input.required !== false,
        ...(str(input.env) ? { env: str(input.env) } : {}), ...(str(input.default) ? { default: str(input.default) } : {}), ...(str(input.help) ? { help: str(input.help) } : {}) };
}

export function readConnector(value: unknown): Connector | null {
    const c = record(value);
    if (!str(c.id)) return null;
    const auth = str(c.auth);
    const state = str(c.state);
    const secrets = record(c.secrets);
    return {
        id: str(c.id), name: str(c.name) || str(c.id), vendor: str(c.vendor), category: str(c.category) || "Other", description: str(c.description),
        docs: str(c.docs), auth: auth === "token" || auth === "optional-token" ? auth : "none",
        inputs: (Array.isArray(c.inputs) ? c.inputs : []).map(readInput).filter((input): input is ConnectorInput => Boolean(input)),
        requires: strings(c.requires), notes: str(c.notes), tools: strings(c.tools), transport: c.transport === "local" ? "local" : "remote",
        installed: c.installed === true, available: c.available !== false, missing: strings(c.missing),
        secrets: Object.fromEntries(Object.entries(secrets).map(([key, set]) => [key, set === true])),
        ...(typeof c.enabled === "boolean" ? { enabled: c.enabled } : {}),
        ...(STATES.has(state) ? { state: state as Connector["state"] } : {}),
        ...(str(c.error) ? { error: str(c.error) } : {}),
        ...(typeof c.tool_count === "number" ? { tool_count: c.tool_count } : {}),
    };
}

const one = (value: unknown) => readConnector(record(value).connector);

export const connectorsService = {
    async list(signal?: AbortSignal): Promise<Connector[]> {
        const data = record((await apiClient.get("/mcp/connectors", { signal })).data);
        return (Array.isArray(data.connectors) ? data.connectors : []).map(readConnector).filter((c): c is Connector => Boolean(c));
    },
    async connect(id: string, values: Record<string, string>, secrets: Record<string, string>) {
        return one((await apiClient.post(`/mcp/connectors/${encodeURIComponent(id)}`, { values, secrets })).data);
    },
    async setEnabled(id: string, enabled: boolean) {
        return one((await apiClient.patch(`/mcp/connectors/${encodeURIComponent(id)}`, { enabled })).data);
    },
    async disconnect(id: string) { await apiClient.delete(`/mcp/connectors/${encodeURIComponent(id)}`); },
    async clients(signal?: AbortSignal): Promise<ClientInfo[]> {
        const data = record((await apiClient.get("/mcp/clients", { signal })).data);
        return (Array.isArray(data.clients) ? data.clients : []).map(record).filter((c) => str(c.id))
            .map((c) => ({ id: str(c.id), name: str(c.name) || str(c.id), path: str(c.path) || null, note: str(c.note) }));
    },
    async clientConfig(id: string, http: boolean): Promise<ClientConfig> {
        const c = record((await apiClient.get(`/mcp/clients/${encodeURIComponent(id)}${http ? "?http=true" : ""}`)).data);
        return { client: str(c.client), name: str(c.name), snippet: str(c.snippet), path: str(c.path) || null, note: str(c.note), command: str(c.command) };
    },
};
