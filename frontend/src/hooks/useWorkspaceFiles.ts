import { useCallback, useEffect, useRef, useState } from "react";
import { deleteFile, listFiles } from "../services/files.ts";
import type { WorkspaceFile } from "../types/protocol.ts";

export function useWorkspaceFiles(conversationId: string | null) {
    const [listing, setListing] = useState<{ conversationId: string | null; files: WorkspaceFile[] }>({ conversationId: null, files: [] });
    const [loading, setLoading] = useState(false);
    const currentId = useRef(conversationId);
    currentId.current = conversationId;
    const request = useRef<AbortController | null>(null);

    const refresh = useCallback(async () => {
        if (currentId.current !== conversationId) return;
        request.current?.abort();
        if (!conversationId) {
            request.current = null;
            setListing({ conversationId: null, files: [] }); setLoading(false);
            return;
        }
        const controller = new AbortController();
        request.current = controller;
        setLoading(true);
        try {
            const files = await listFiles(conversationId, controller.signal);
            if (!controller.signal.aborted && currentId.current === conversationId) setListing({ conversationId, files });
        } catch {
            // A new conversation may not yet have a workspace. Preserve its own prior listing on transient errors.
        } finally {
            if (request.current === controller) { request.current = null; setLoading(false); }
        }
    }, [conversationId]);

    useEffect(() => {
        void refresh();
        return () => request.current?.abort();
    }, [refresh]);

    const remove = useCallback(async (path: string) => {
        if (!conversationId) return;
        await deleteFile(conversationId, path);
        await refresh();
    }, [conversationId, refresh]);

    return { files: listing.conversationId === conversationId ? listing.files : [], loading, refresh, remove };
}
