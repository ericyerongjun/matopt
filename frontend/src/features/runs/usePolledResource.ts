import { useEffect, useRef, useState } from "react";
import { isUnavailable } from "../../services/api.ts";

export type PolledState<T> = { data: T | null; error: boolean; unavailable: boolean };

/** Loads once, then every `intervalMs` while `active`; like the Usage tab, aborted on change. */
export function usePolledResource<T>(key: string | null, load: (key: string, signal: AbortSignal) => Promise<T>,
    active: (data: T | null) => boolean, revision = 0, intervalMs = 2000): PolledState<T> {
    const [state, setState] = useState<PolledState<T>>({ data: null, error: false, unavailable: false });
    const loadRef = useRef(load); loadRef.current = load;
    const activeRef = useRef(active); activeRef.current = active;
    useEffect(() => {
        setState({ data: null, error: false, unavailable: false });
        if (!key) return;
        const controller = new AbortController();
        let timer: ReturnType<typeof setTimeout> | undefined;
        const run = async () => {
            let latest: T | null = null;
            try {
                latest = await loadRef.current(key, controller.signal);
                if (!controller.signal.aborted) setState({ data: latest, error: false, unavailable: false });
            } catch (error) {
                if (controller.signal.aborted) return;
                if (isUnavailable(error)) { setState({ data: null, error: false, unavailable: true }); return; }
                setState((previous) => ({ ...previous, error: true }));
            }
            if (!controller.signal.aborted && activeRef.current(latest)) timer = setTimeout(run, intervalMs);
        };
        void run();
        return () => { controller.abort(); if (timer !== undefined) clearTimeout(timer); };
    }, [key, revision, intervalMs]);
    return state;
}
