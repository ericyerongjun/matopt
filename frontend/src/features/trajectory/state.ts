import type { EventPage, SessionEvent, TrajectoryProjection, TrajectoryRecord } from "./types.ts";

export const RECORD_PAGE_SIZE = 50;
export const EVENT_PAGE_SIZE = 200;

export interface EventWindow {
    conversationId: string | null;
    events: SessionEvent[];
    nextSeq: number;
}

export type EventWindowAction =
    | { type: "reset"; conversationId: string | null }
    | { type: "page"; conversationId: string; page: EventPage };

/** Stable sequence keys survive prepends, overlapping pages, and live replay. */
export function eventWindowReducer(state: EventWindow, action: EventWindowAction): EventWindow {
    if (action.type === "reset") return { conversationId: action.conversationId, events: [], nextSeq: -1 };
    if (action.conversationId !== state.conversationId) return state;
    const merged = new Map(state.events.map((event) => [event.seq, event]));
    for (const event of action.page.events) merged.set(event.seq, event);
    return {
        ...state,
        events: [...merged.values()].sort((a, b) => a.seq - b.seq),
        nextSeq: Math.max(state.nextSeq, action.page.next_seq),
    };
}

export function flattenRecords(projection: TrajectoryProjection | null): TrajectoryRecord[] {
    return projection?.turns.flatMap((turn) => turn.steps.flatMap((step) => step.records)).sort((a, b) => a.seq - b.seq) ?? [];
}

export function requestSequence(record: TrajectoryRecord): number | null {
    if (!record.type.startsWith("assistant/") || record.input === null || typeof record.input !== "object") return null;
    const seq = (record.input as Record<string, unknown>).request_seq;
    return typeof seq === "number" && Number.isSafeInteger(seq) && seq >= 0 ? seq : null;
}

/** The first visible sequence is anchored; appending records never evicts it. */
export function visibleRecords(records: TrajectoryRecord[], anchorSeq: number | null): TrajectoryRecord[] {
    if (anchorSeq === null) return records.slice(-RECORD_PAGE_SIZE);
    return records.filter((record) => record.seq >= anchorSeq);
}

export function olderAnchor(records: TrajectoryRecord[], firstVisibleSeq: number): number | null {
    const index = records.findIndex((record) => record.seq === firstVisibleSeq);
    return index > 0 ? records[Math.max(0, index - RECORD_PAGE_SIZE)].seq : null;
}

export function matchesRecord(record: TrajectoryRecord, query: string, range: [number, number] | null): boolean {
    if (range !== null) {
        if (record.started_at === null) return false;
        if ((record.ended_at ?? record.started_at) < range[0] || record.started_at > range[1]) return false;
    }
    if (!query.trim()) return true;
    const searchable = `${record.label} ${record.type} ${record.status} ${JSON.stringify(record.input)} ${JSON.stringify(record.output)}`;
    return searchable.toLowerCase().includes(query.trim().toLowerCase());
}

export function durationLabel(milliseconds: number | null | undefined): string {
    if (milliseconds == null || !Number.isFinite(milliseconds)) return "Unknown";
    return milliseconds < 1000 ? `${Math.round(milliseconds)} ms` : `${(milliseconds / 1000).toFixed(2)} s`;
}

export function tokenLabel(value: number | null | undefined): string {
    return value == null ? "Unknown" : value.toLocaleString();
}
