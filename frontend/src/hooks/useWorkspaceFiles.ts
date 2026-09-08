import { useCallback, useEffect, useState } from "react";
import { deleteFile, listFiles } from "../services/files";
import type { WorkspaceFile } from "../types/protocol";

export function useWorkspaceFiles(conversationId: string | null) {
    const [files, setFiles] = useState<WorkspaceFile[]>([]);
    const [loading, setLoading] = useState(false);

    const refresh = useCallback(async () => {
        if (!conversationId) {
            setFiles([]);
            return;
        }
        setLoading(true);
        try {
            setFiles(await listFiles(conversationId));
        } catch {
            /* listing can 404 if the conversation was just created */
        } finally {
            setLoading(false);
        }
    }, [conversationId]);

    useEffect(() => {
        void refresh();
    }, [refresh]);

    const remove = useCallback(
        async (path: string) => {
            if (!conversationId) return;
            await deleteFile(conversationId, path);
            await refresh();
        },
        [conversationId, refresh]
    );

    return { files, loading, refresh, remove };
}
