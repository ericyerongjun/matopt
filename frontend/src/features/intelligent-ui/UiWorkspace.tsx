import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Artifact } from "../../types/protocol.ts";
import type { JSONValue, UICapabilities, UISurface } from "../../types/ui.ts";
import { executeSurfaceAction, persistSurfaceState } from "./api.ts";
import { actionValues, computedSurfaceState, declaredStateUpdate, initialSurfaceState, isSurface, resetSurfaceState, surfaceExplanation } from "./model.ts";

export interface SurfaceRecord {
    surface: UISurface;
    state: Record<string, JSONValue>;
    initialState: Record<string, JSONValue>;
    resultState: Record<string, JSONValue>;
    viewState: Record<string, string | boolean>;
    busy: boolean;
    saving: boolean;
    error: string | null;
    editVersion: number;
    savedVersion: number;
}
export type CanvasSelection = { kind: "surface"; surfaceId: string } | { kind: "artifact"; artifact: Artifact; conversationId: string | null } | null;
interface UiWorkspace {
    conversationId: string | null;
    conversationBusy: boolean;
    capabilities: UICapabilities | null;
    capabilitiesLoaded: boolean;
    records: Record<string, SurfaceRecord>;
    selection: CanvasSelection;
    registerSurface: (surface: UISurface) => void;
    openSurface: (surface: UISurface) => void;
    openArtifact: (artifact: Artifact, conversationId?: string | null) => void;
    closeCanvas: () => void;
    updateState: (id: string, patch: Record<string, JSONValue>) => void;
    updateViewState: (id: string, nodeId: string, value: string | boolean) => void;
    resetState: (id: string) => void;
    runAction: (id: string, actionId: string) => void;
    stopAction: (id: string) => void;
    reloadSurface: (id: string) => void;
    explainSurface: (id: string) => void;
    canExplain: boolean;
}
const Context = createContext<UiWorkspace | null>(null);

export function UiWorkspaceProvider({ conversationId, conversationBusy = false, surfaceIds, capabilities: suppliedCapabilities, onExplain, onCanvasOpen, children }: {
    conversationId: string | null;
    conversationBusy?: boolean;
    /** Complete set from all presented chat turns, including validated live previews. */
    surfaceIds?: readonly string[];
    capabilities?: UICapabilities;
    onExplain?: (text: string) => void;
    onCanvasOpen?: () => void;
    children: ReactNode;
}) {
    const [records, setRecords] = useState<Record<string, SurfaceRecord>>(() => Object.create(null));
    const [capabilities, setCapabilities] = useState<UICapabilities | null>(suppliedCapabilities ?? null);
    const [capabilitiesLoaded, setCapabilitiesLoaded] = useState(Boolean(suppliedCapabilities));
    const [selection, setSelection] = useState<CanvasSelection>(null);
    const anyActionBusy = Object.values(records).some((record) => record.busy);
    const live = useRef(records);
    const allowedSurfaces = useMemo(() => surfaceIds === undefined ? null : new Set(surfaceIds), [surfaceIds]);
    const allowed = useRef(allowedSurfaces); allowed.current = allowedSurfaces;
    const chatBusy = useRef(conversationBusy); chatBusy.current = conversationBusy;
    const generation = useRef(0);
    const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
    const saves = useRef(new Map<string, Promise<void>>());
    const controllers = useRef(new Set<AbortController>());
    const controllerOwners = useRef(new Map<AbortController, string>());
    const actionControllers = useRef(new Map<string, AbortController>());
    const opener = useRef<HTMLElement | null>(null);
    const callbacks = useRef({ onExplain, onCanvasOpen });
    callbacks.current = { onExplain, onCanvasOpen };
    useEffect(() => {
        if (suppliedCapabilities) { setCapabilities(suppliedCapabilities); setCapabilitiesLoaded(true); return; }
        const controller = new AbortController();
        const timeout = setTimeout(() => { setCapabilities(null); setCapabilitiesLoaded(true); controller.abort(); }, 10_000);
        void fetch("/api/ui/capabilities", { signal: controller.signal }).then(async (response) => {
            if (!response.ok) throw new Error("Interactive views are unavailable.");
            const next = await response.json();
            if (next.version !== 1 || typeof next.native !== "boolean" || typeof next.mini_apps !== "boolean" || !Array.isArray(next.node_types)) throw new Error("Unsupported interactive views.");
            if (!controller.signal.aborted) { setCapabilities(next); setCapabilitiesLoaded(true); }
        }).catch(() => { if (!controller.signal.aborted) { setCapabilities(null); setCapabilitiesLoaded(true); } })
            .finally(() => clearTimeout(timeout));
        return () => { clearTimeout(timeout); controller.abort(); };
    }, [suppliedCapabilities]);
    const publish = useCallback((id: string, record: SurfaceRecord) => {
        if (allowed.current && !allowed.current.has(id)) return;
        live.current = Object.assign(Object.create(null), live.current, { [id]: record });
        setRecords(live.current);
    }, []);
    const clearPending = useCallback(() => {
        generation.current++;
        for (const timer of timers.current.values()) clearTimeout(timer);
        timers.current.clear();
        for (const controller of controllers.current) controller.abort();
        controllers.current.clear(); controllerOwners.current.clear(); actionControllers.current.clear(); saves.current.clear();
    }, []);
    useLayoutEffect(() => {
        clearPending(); live.current = Object.create(null); setRecords(live.current); setSelection(null);
        return clearPending;
    }, [conversationId, clearPending]);

    useLayoutEffect(() => {
        if (!allowedSurfaces) return;
        const removed = Object.keys(live.current).filter((id) => !allowedSurfaces.has(id));
        if (!removed.length) return;
        const next = Object.assign(Object.create(null), live.current);
        for (const id of removed) {
            clearTimeout(timers.current.get(id)); timers.current.delete(id);
            saves.current.delete(id);
            for (const [controller, owner] of controllerOwners.current) {
                if (owner !== id) continue;
                controller.abort(); controllers.current.delete(controller); controllerOwners.current.delete(controller);
            }
            actionControllers.current.delete(id);
            delete next[id];
        }
        live.current = next; setRecords(next);
        setSelection((current) => current?.kind === "surface" && !allowedSurfaces.has(current.surfaceId) ? null : current);
    }, [allowedSurfaces]);

    const registerSurface = useCallback((surface: UISurface) => {
        if (!isSurface(surface) || allowed.current && !allowed.current.has(surface.id)) return;
        const existing = live.current[surface.id];
        if (existing && (existing.surface.revision > surface.revision || JSON.stringify(existing.surface) === JSON.stringify(surface))) return;
        // A partial revision must never replace a result that is already usable.
        if (existing?.surface.status === "committed" && surface.status === "preview") return;
        const state = initialSurfaceState(surface);
        publish(surface.id, {
            surface, state: existing && (existing.busy || existing.editVersion > existing.savedVersion) ? existing.state : state,
            initialState: resetSurfaceState(surface, existing?.initialState),
            resultState: "computed_state" in surface ? computedSurfaceState(surface) : existing && (existing.busy || existing.saving || existing.editVersion > existing.savedVersion || existing.surface.revision === surface.revision) ? existing.resultState : state,
            viewState: existing?.viewState ?? {},
            busy: existing?.busy ?? false, saving: existing?.saving ?? false, error: null,
            editVersion: existing?.editVersion ?? 0, savedVersion: existing?.savedVersion ?? 0,
        });
    }, [publish]);

    const flushState = useCallback((id: string, actionOwner?: string): Promise<void> => {
        const current = saves.current.get(id);
        if (current) return current;
        const runGeneration = generation.current;
        const promise = (async () => {
            while (generation.current === runGeneration) {
                const record = live.current[id];
                if (!record || !conversationId || chatBusy.current || Object.values(live.current).some((other) => other.busy && other.surface.id !== actionOwner)
                    || record.surface.status !== "committed" || record.editVersion <= record.savedVersion) return;
                const controller = new AbortController(); controllers.current.add(controller); controllerOwners.current.set(controller, id);
                const timeout = setTimeout(() => controller.abort(), 30_000);
                publish(id, { ...record, saving: true });
                try {
                    const surface = await persistSurfaceState(conversationId, record.surface, record.state, controller.signal);
                    if (generation.current !== runGeneration || controller.signal.aborted) return;
                    const latest = live.current[id]; if (!latest) return;
                    if (surface.revision < latest.surface.revision) throw new Error("This result changed while saving. Reload its saved version to continue.");
                    publish(id, {
                        ...latest, surface, savedVersion: record.editVersion, saving: false, error: null,
                        state: latest.editVersion === record.editVersion ? initialSurfaceState(surface) : latest.state,
                    });
                } catch (error) {
                    if (generation.current === runGeneration && controllerOwners.current.get(controller) === id && live.current[id]) {
                        publish(id, { ...live.current[id], saving: false, error: error instanceof Error ? error.message : "Parameters could not be saved." });
                    }
                    throw error;
                } finally { clearTimeout(timeout); controllers.current.delete(controller); controllerOwners.current.delete(controller); }
            }
        })().finally(() => { if (saves.current.get(id) === promise) saves.current.delete(id); });
        saves.current.set(id, promise);
        return promise;
    }, [conversationId, publish]);

    useEffect(() => {
        if (!conversationBusy && !anyActionBusy) for (const record of Object.values(live.current)) {
            if (record.editVersion > record.savedVersion) void flushState(record.surface.id).catch(() => {});
        }
    }, [conversationBusy, anyActionBusy, flushState]);

    const updateState = useCallback((id: string, patch: Record<string, JSONValue>) => {
        const record = live.current[id];
        if (!record || record.busy || record.surface.status !== "committed") return;
        const state = declaredStateUpdate(patch, record.state);
        if (!state || JSON.stringify(state) === JSON.stringify(record.state)) return;
        publish(id, { ...record, state, editVersion: record.editVersion + 1, error: null });
        clearTimeout(timers.current.get(id));
        timers.current.set(id, setTimeout(() => { timers.current.delete(id); void flushState(id).catch(() => {}); }, 400));
    }, [flushState, publish]);

    const runAction = useCallback((id: string, actionId: string) => {
        const first = live.current[id];
        if (!first || !conversationId || chatBusy.current || Object.values(live.current).some((record) => record.busy) || first.surface.status !== "committed" || !(first.surface.kind === "native" ? capabilities?.native : capabilities?.mini_apps) || !first.surface.actions.some((action) => action.id === actionId)) return;
        clearTimeout(timers.current.get(id)); timers.current.delete(id);
        const runGeneration = generation.current;
        const controller = new AbortController(); controllers.current.add(controller); controllerOwners.current.set(controller, id); actionControllers.current.set(id, controller);
        publish(id, { ...first, busy: true, error: null });
        void (async () => {
            try {
                await flushState(id, id);
                if (controller.signal.aborted || generation.current !== runGeneration) return;
                const record = live.current[id];
                const action = record.surface.actions.find((item) => item.id === actionId);
                if (!action) throw new Error("This computation is no longer available for the current result.");
                await executeSurfaceAction(conversationId, record.surface, actionId, actionValues(action, record.state), crypto.randomUUID(), controller.signal, (surface) => {
                    if (generation.current !== runGeneration || controller.signal.aborted) return;
                    const current = live.current[id];
                    if (!current || surface.id !== id || surface.revision < current.surface.revision) return;
                    publish(id, { ...current, surface, state: initialSurfaceState(surface), initialState: resetSurfaceState(surface, current.initialState), resultState: computedSurfaceState(surface), savedVersion: current.editVersion, error: null });
                });
            } catch (error) {
                if (generation.current === runGeneration && controllerOwners.current.get(controller) === id && live.current[id]) {
                    publish(id, { ...live.current[id], error: controller.signal.aborted ? "Computation stopped. The previous result is still available." : error instanceof Error ? error.message : "The computation could not finish." });
                }
            } finally {
                const owned = controllerOwners.current.get(controller) === id;
                controllers.current.delete(controller); controllerOwners.current.delete(controller);
                if (actionControllers.current.get(id) === controller) actionControllers.current.delete(id);
                if (owned && generation.current === runGeneration && live.current[id]) publish(id, { ...live.current[id], busy: false });
            }
        })();
    }, [conversationId, capabilities, flushState, publish]);

    const reloadSurface = useCallback((id: string) => {
        if (!conversationId || live.current[id]?.busy) return;
        const runGeneration = generation.current;
        const requested = live.current[id];
        const controller = new AbortController(); controllers.current.add(controller); controllerOwners.current.set(controller, id);
        clearTimeout(timers.current.get(id)); timers.current.delete(id);
        void fetch(`/api/conversations/${encodeURIComponent(conversationId)}/ui/${encodeURIComponent(id)}`, { signal: controller.signal })
            .then(async (response) => { if (!response.ok) throw new Error("The saved result could not be loaded."); return response.json(); })
            .then((body) => {
                if (runGeneration !== generation.current || controller.signal.aborted || !isSurface(body.surface)) return;
                const record = live.current[id];
                if (record && requested && record.editVersion === requested.editVersion && body.surface.revision >= record.surface.revision) {
                    publish(id, { ...record, surface: body.surface, state: initialSurfaceState(body.surface), initialState: resetSurfaceState(body.surface, record.initialState), resultState: "computed_state" in body.surface ? computedSurfaceState(body.surface) : record.resultState, savedVersion: record.editVersion, error: null });
                }
            }).catch((error) => {
                if (runGeneration === generation.current && controllerOwners.current.get(controller) === id && live.current[id]) publish(id, { ...live.current[id], error: error.message });
            }).finally(() => { controllers.current.delete(controller); controllerOwners.current.delete(controller); });
    }, [conversationId, publish]);

    const openSurface = useCallback((surface: UISurface) => {
        if (allowed.current && !allowed.current.has(surface.id)) return;
        registerSurface(surface); opener.current = document.activeElement as HTMLElement | null;
        setSelection({ kind: "surface", surfaceId: surface.id }); callbacks.current.onCanvasOpen?.();
    }, [registerSurface]);
    const openArtifact = useCallback((artifact: Artifact, artifactConversationId?: string | null) => {
        opener.current = document.activeElement as HTMLElement | null;
        setSelection({ kind: "artifact", artifact, conversationId: artifactConversationId ?? conversationId }); callbacks.current.onCanvasOpen?.();
    }, [conversationId]);
    const closeCanvas = useCallback(() => {
        setSelection(null);
        requestAnimationFrame(() => { if (opener.current?.isConnected) opener.current.focus(); });
    }, []);
    const value: UiWorkspace = {
        conversationId, conversationBusy: conversationBusy || anyActionBusy, capabilities, capabilitiesLoaded, records, selection, registerSurface, openSurface, openArtifact, closeCanvas, updateState, runAction, reloadSurface,
        updateViewState: (id, nodeId, viewValue) => { const record = live.current[id]; if (record && record.viewState[nodeId] !== viewValue) publish(id, { ...record, viewState: { ...record.viewState, [nodeId]: viewValue } }); },
        resetState: (id) => { const record = live.current[id]; if (record) updateState(id, record.initialState); },
        stopAction: (id) => actionControllers.current.get(id)?.abort(),
        explainSurface: (id) => { const record = live.current[id]; if (record) callbacks.current.onExplain?.(surfaceExplanation(record.surface, record.state, record.resultState)); },
        canExplain: Boolean(onExplain),
    };
    return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useOptionalUiWorkspace() { return useContext(Context); }
export function useUiWorkspace(): UiWorkspace {
    const context = useContext(Context);
    if (!context) throw new Error("useUiWorkspace requires UiWorkspaceProvider");
    return context;
}

export function useSurface(surface: UISurface) {
    const workspace = useUiWorkspace();
    useEffect(() => workspace.registerSurface(surface), [surface, workspace.registerSurface]);
    return { workspace, record: workspace.records[surface.id] ?? {
        surface, state: initialSurfaceState(surface), initialState: resetSurfaceState(surface), resultState: computedSurfaceState(surface), viewState: {}, busy: false, saving: false, error: null, editVersion: 0, savedVersion: 0,
    } };
}
