/**
 * useExport — trigger content export and file download.
 */

import { useState, useCallback } from "react";
import { exportContent } from "../services/export";
import type { ExportFormat } from "../types/export";

export function useExport() {
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const doExport = useCallback(
        async (content: string, format: ExportFormat, title?: string) => {
            setLoading(true);
            setError(null);
            try {
                await exportContent({ content, format, title });
            } catch (err: unknown) {
                setError(err instanceof Error ? err.message : "Export failed");
            } finally {
                setLoading(false);
            }
        },
        []
    );

    return { loading, error, doExport };
}
