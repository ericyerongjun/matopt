import type { JSONValue } from "../../types/ui.ts";
import { declaredStateUpdate } from "./model.ts";

export interface FrameIdentity { channel: string; surfaceId: string }
export type FrameMessage = { type: "matopt:ready" } | { type: "matopt:resize"; height: number } | { type: "matopt:state"; state: Record<string, JSONValue> };

export function validateFrameMessage(value: unknown, identity: FrameIdentity, state: Record<string, JSONValue>): FrameMessage | null {
    if (!value || typeof value !== "object") return null;
    const message = value as Record<string, unknown>;
    if (message.version !== 1 || message.channel !== identity.channel || message.surfaceId !== identity.surfaceId) return null;
    if (message.type === "matopt:ready") return { type: message.type };
    if (message.type === "matopt:resize" && typeof message.height === "number" && Number.isFinite(message.height)) {
        return { type: message.type, height: Math.max(160, Math.min(1200, Math.ceil(message.height))) };
    }
    if (message.type === "matopt:state") {
        const next = declaredStateUpdate(message.state, state);
        if (next) return { type: message.type, state: next };
    }
    return null;
}

export function sandboxOrigin(host: { origin: string; protocol: string; hostname: string; port: string }, configured?: string): string | null {
    try {
        const url = new URL(configured || host.origin);
        if (!configured) url.port = host.port === "5173" ? "5174" : "3001";
        if (!["http:", "https:"].includes(url.protocol) || url.origin === host.origin || url.username || url.password) return null;
        return url.origin;
    } catch { return null; }
}
