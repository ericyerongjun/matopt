import apiClient from "./api.ts";
import { streamChat, streamRun, type ChatStreamBody } from "./stream.ts";
import type { StreamEvent } from "../types/protocol.ts";

export interface RunSnapshot {
    id: string;
    conversation_id: string;
    message_id: string | null;
    status: "queued" | "running" | "paused" | "completed" | "stopped" | "failed" | "interrupted";
    created_at: number;
    updated_at: number;
    last_seq: number;
}

export async function latestRun(cid: string, signal?: AbortSignal): Promise<RunSnapshot | null> {
    const { data } = await apiClient.get<{ run?: RunSnapshot | null }>(`/chat/${encodeURIComponent(cid)}/run`, { signal });
    return data.run ?? null;
}

export async function controlRun(cid: string, action: "pause" | "resume") {
    await apiClient.post(`/chat/${encodeURIComponent(cid)}/${action}`);
}

function retryDelay(ms: number, signal: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
        const aborted = () => { clearTimeout(timer); reject(new DOMException("Subscription closed", "AbortError")); };
        const timer = setTimeout(() => { signal.removeEventListener("abort", aborted); resolve(); }, ms);
        if (signal.aborted) aborted();
        else signal.addEventListener("abort", aborted, { once: true });
    });
}

/** The subscriber can disappear at any time. Only an explicit server terminal ends a run. */
export async function observeRun(options: {
    body?: ChatStreamBody;
    runId?: string;
    after?: number;
    signal: AbortSignal;
    onEvent: (event: StreamEvent) => void;
    onConnection?: (reconnecting: boolean) => void;
}): Promise<"done" | "stopped" | "error"> {
    let runId = options.runId;
    let cursor = options.after ?? 0;
    let failures = 0;
    const received = (event: StreamEvent) => {
        if (typeof event.run_id === "string") {
            if (runId && runId !== event.run_id) return;
            runId = event.run_id;
            const sequence = Number(event.seq);
            if (Number.isSafeInteger(sequence) && sequence > 0) {
                if (sequence <= cursor) return;
                cursor = sequence;
            }
        }
        failures = 0;
        options.onConnection?.(false);
        options.onEvent(event);
    };
    while (!options.signal.aborted) {
        try {
            if (runId) return await streamRun(runId, cursor, received, options.signal);
            if (!options.body) throw new Error("A run or submission is required");
            return await streamChat(options.body, received, options.signal);
        } catch (error) {
            if (options.signal.aborted || (error as Error).name === "AbortError") throw error;
            const status = (error as { status?: number }).status;
            // A rejected request is definitive. Network failures are not.
            if (status && status >= 400 && status < 500 && status !== 408 && status !== 429) throw error;
            // Legacy, unsequenced servers cannot safely replay a submitted turn.
            if (!runId && !options.body?.client_run_id) throw error;
            options.onConnection?.(true);
            await retryDelay(Math.min(250 * 2 ** Math.min(failures++, 5), 5000), options.signal);
        }
    }
    throw new DOMException("Subscription closed", "AbortError");
}
