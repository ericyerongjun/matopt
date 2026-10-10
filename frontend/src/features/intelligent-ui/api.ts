import type { JSONValue, UISurface } from "../../types/ui.ts";
import { isSurface } from "./model.ts";

function endpoint(conversationId: string, surfaceId: string) {
    return `/api/conversations/${encodeURIComponent(conversationId)}/ui/${encodeURIComponent(surfaceId)}`;
}

async function checkResponse(response: Response): Promise<Response> {
    if (response.ok) return response;
    let detail = `Request failed (${response.status})`;
    try {
        const body = await response.json();
        if (typeof body.detail === "string") detail = body.detail;
        else if (typeof body.error === "string") detail = body.error;
    } catch { /* Preserve the useful HTTP status when there is no JSON error. */ }
    throw new Error(detail);
}

export async function persistSurfaceState(conversationId: string, surface: UISurface, state: Record<string, JSONValue>, signal: AbortSignal): Promise<UISurface> {
    const response = await checkResponse(await fetch(`${endpoint(conversationId, surface.id)}/state`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, signal,
        body: JSON.stringify({ revision: surface.revision, state }),
    }));
    const body = await response.json();
    if (!isSurface(body.surface)) throw new Error("The saved interactive result is invalid. Your previous result is still available.");
    return body.surface;
}

export async function executeSurfaceAction(conversationId: string, surface: UISurface, actionId: string, values: Record<string, JSONValue>, idempotencyKey: string, signal: AbortSignal,
    onSurface: (surface: UISurface) => void): Promise<void> {
    const response = await checkResponse(await fetch(`${endpoint(conversationId, surface.id)}/actions`, {
        method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" }, signal,
        body: JSON.stringify({ revision: surface.revision, action_id: actionId, values, idempotency_key: idempotencyKey }),
    }));
    if (response.headers.get("content-type")?.includes("application/json")) {
        const body = await response.json();
        if (!isSurface(body.surface)) throw new Error("The action returned an invalid result.");
        onSurface(body.surface); return;
    }
    if (!response.body) throw new Error("The computation returned no result.");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let committed = false;
    const frame = (raw: string) => {
        const lines = raw.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart());
        if (!lines.length) return;
        let event: Record<string, unknown>;
        try { event = JSON.parse(lines.join("\n")); } catch { throw new Error("The computation returned an unreadable event."); }
        if (event.type === "ui_error" || event.type === "error") throw new Error(typeof event.error === "string" ? event.error : "The computation could not finish.");
        if (["ui_start", "ui_patch", "ui_commit"].includes(String(event.type)) && isSurface(event.surface)) {
            // Keep the previous usable result while a new revision is being computed.
            if (event.surface.status === "committed") { committed = true; onSurface(event.surface); }
        }
    };
    try {
        while (true) {
            const { value, done } = await reader.read();
            buffer += decoder.decode(value, { stream: !done });
            buffer = buffer.replace(/\r\n/g, "\n");
            let end = buffer.indexOf("\n\n");
            while (end !== -1) { frame(buffer.slice(0, end)); buffer = buffer.slice(end + 2); end = buffer.indexOf("\n\n"); }
            if (buffer.length > 8_000_000) throw new Error("The interactive result is too large.");
            if (done) break;
        }
        if (buffer.trim()) frame(buffer);
        if (!committed) throw new Error("The computation ended before saving a result. The previous result is still available.");
    } finally {
        try { await reader.cancel(); } catch { /* Aborted readers are already closed. */ }
        reader.releaseLock();
    }
}
