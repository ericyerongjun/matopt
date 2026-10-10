import { useEffect, useReducer, useRef, useState } from "react";
import { telemetryService } from "../../services/telemetry.ts";
import { EVENT_PAGE_SIZE, eventWindowReducer, flattenRecords } from "./state.ts";
import type { TrajectoryProjection, UsageProjection } from "./types.ts";

/** Poll only the active session; old session responses cannot cross the reset. */
export function useSessionTelemetry(conversationId: string | null, running: boolean, revision: number) {
    const [projection, setProjection] = useState<TrajectoryProjection | null>(null);
    const [usage, setUsage] = useState<UsageProjection | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [refreshRevision, setRefreshRevision] = useState(0);
    const [window, dispatch] = useReducer(eventWindowReducer, { conversationId, events: [], nextSeq: -1 });
    const activeRef = useRef(conversationId);
    activeRef.current = conversationId;
    const cursorRef = useRef(-1);
    const olderRequestRef = useRef<AbortController | null>(null);

    useEffect(() => {
        cursorRef.current = -1;
        dispatch({ type: "reset", conversationId });
        setProjection(null);
        setUsage(null);
        setError(null);
        olderRequestRef.current?.abort();
        return () => olderRequestRef.current?.abort();
    }, [conversationId]);

    useEffect(() => {
        if (!conversationId) return;
        const controller = new AbortController();
        let timer: ReturnType<typeof setTimeout> | undefined;
        const refresh = async () => {
            setLoading(true);
            try {
                const [nextProjection, nextUsage] = await Promise.all([
                    telemetryService.trajectory(conversationId, controller.signal),
                    telemetryService.usage(conversationId, controller.signal),
                ]);
                if (controller.signal.aborted) return;
                setProjection(nextProjection);
                setUsage(nextUsage);
                // Start with the tail of a large log. Older pages are explicit,
                // so opening the UI does not download the full event history.
                const records = flattenRecords(nextProjection);
                const fromSeq = cursorRef.current < 0
                    ? Math.max(-1, (records.at(-1)?.seq ?? -1) - EVENT_PAGE_SIZE)
                    : cursorRef.current;
                const page = await telemetryService.events(conversationId, fromSeq, EVENT_PAGE_SIZE, controller.signal);
                if (controller.signal.aborted) return;
                dispatch({ type: "page", conversationId, page });
                cursorRef.current = Math.max(cursorRef.current, page.next_seq);
                setError(null);
                // Catch up in bounded pages if a burst exceeded the page size.
                if (page.has_more) timer = setTimeout(refresh, 0);
                else if (running || nextProjection.turns.some((turn) => turn.status === "running")) timer = setTimeout(refresh, 2000);
            } catch (failure) {
                if (controller.signal.aborted) return;
                setError(failure instanceof Error ? failure.message : "Unable to load session details");
                if (running) timer = setTimeout(refresh, 4000);
            } finally {
                if (!controller.signal.aborted) setLoading(false);
            }
        };
        void refresh();
        return () => { controller.abort(); if (timer !== undefined) clearTimeout(timer); };
    }, [conversationId, running, revision, refreshRevision]);

    const loadOlder = async (beforeSeq: number) => {
        if (!conversationId || beforeSeq <= 0) return;
        olderRequestRef.current?.abort();
        const controller = new AbortController();
        olderRequestRef.current = controller;
        const fromSeq = Math.max(-1, beforeSeq - EVENT_PAGE_SIZE - 1);
        try {
            const page = await telemetryService.events(conversationId, fromSeq, Math.min(EVENT_PAGE_SIZE, beforeSeq - fromSeq - 1), controller.signal);
            if (controller.signal.aborted || activeRef.current !== conversationId) return;
            dispatch({ type: "page", conversationId, page });
        } catch (failure) {
            if (!controller.signal.aborted && activeRef.current === conversationId) {
                setError(failure instanceof Error ? failure.message : "Unable to load older events");
            }
        }
    };

    return { projection, usage, events: window.events, loading, error, loadOlder, refresh: () => setRefreshRevision((v) => v + 1) };
}
