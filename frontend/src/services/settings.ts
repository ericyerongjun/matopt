/** GET /api/settings and feature toggles. Security values are read-only here by design. */
import apiClient from "./api.ts";

export type FeatureStage = "stable" | "beta" | "experimental" | "deprecated" | string;
export interface FeatureFlag {
    key: string;
    name: string;
    description: string;
    stage: FeatureStage;
    enabled: boolean;
    /** False when a change takes effect only after a server restart. */
    live: boolean;
    origin: string;
}
export interface McpServerStatus {
    name: string;
    state: "connected" | "failed" | "disabled";
    tools: number;
    resources: number;
    error?: string;
}
export interface SecuritySettings {
    sandbox: { mode: string; network: string; backend: string; available: boolean };
    hooks: { enabled: boolean; path: string; events: string[] };
    exec_rules: { path: string; rules: number };
    approvals_reviewer: "off" | "guardian" | "user";
    redact_tool_output: boolean;
}
export interface SettingsSnapshot {
    features: FeatureFlag[];
    security: SecuritySettings | null;
    instructions: { operator: { chars: number; sources: string[] } } | null;
    mcp: { servers: McpServerStatus[] };
    config: { path: string; exists: boolean; profile: string | null } | null;
}

const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const str = (value: unknown) => typeof value === "string" ? value : "";
const num = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : Array.isArray(value) ? value.length : 0;
const strings = (value: unknown) => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];

export function readFeature(value: unknown): FeatureFlag | null {
    const feature = record(value);
    if (!str(feature.key)) return null;
    return { key: str(feature.key), name: str(feature.name) || str(feature.key), description: str(feature.description),
        stage: str(feature.stage) || "stable", enabled: feature.enabled === true, live: feature.live !== false, origin: str(feature.origin) || "default" };
}

/** Validates the payload so a partially implemented backend still renders. */
export function readSettings(value: unknown): SettingsSnapshot {
    const data = record(value);
    const security = record(data.security);
    const sandbox = record(security.sandbox), hooks = record(security.hooks), rules = record(security.exec_rules);
    const operator = record(record(data.instructions).operator);
    const config = record(data.config);
    return {
        features: (Array.isArray(data.features) ? data.features : []).map(readFeature).filter((item): item is FeatureFlag => Boolean(item)),
        security: Object.keys(security).length ? {
            sandbox: { mode: str(sandbox.mode) || "unknown", network: typeof sandbox.network === "boolean" ? (sandbox.network ? "Allowed" : "Blocked") : str(sandbox.network) || "unknown", backend: str(sandbox.backend) || "none", available: sandbox.available === true },
            hooks: { enabled: hooks.enabled === true, path: str(hooks.path), events: strings(hooks.events) },
            exec_rules: { path: str(rules.path), rules: num(rules.rules) },
            approvals_reviewer: ["off", "guardian", "user"].includes(str(security.approvals_reviewer)) ? security.approvals_reviewer as SecuritySettings["approvals_reviewer"] : "off",
            redact_tool_output: security.redact_tool_output === true,
        } : null,
        instructions: Object.keys(operator).length ? { operator: { chars: num(operator.chars), sources: strings(operator.sources) } } : null,
        mcp: { servers: (Array.isArray(record(data.mcp).servers) ? record(data.mcp).servers as unknown[] : []).flatMap((raw): McpServerStatus[] => {
            const server = record(raw);
            if (!str(server.name)) return [];
            const state = ["connected", "failed", "disabled"].includes(str(server.state)) ? server.state as McpServerStatus["state"] : "failed";
            return [{ name: str(server.name), state, tools: num(server.tools), resources: num(server.resources), ...(str(server.error) ? { error: str(server.error) } : {}) }];
        }) },
        config: Object.keys(config).length ? { path: str(config.path), exists: config.exists === true, profile: str(config.profile) || null } : null,
    };
}

export async function getSettings(signal?: AbortSignal): Promise<SettingsSnapshot> {
    return readSettings((await apiClient.get("/settings", { signal })).data);
}

export async function setFeature(key: string, enabled: boolean): Promise<FeatureFlag | null> {
    return readFeature((await apiClient.put(`/features/${encodeURIComponent(key)}`, { enabled })).data);
}
