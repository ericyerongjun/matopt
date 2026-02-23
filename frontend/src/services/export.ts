/**
 * Export service — generate PDF / LaTeX / Markdown downloads.
 */

import apiClient from "./api";
import type { ExportOptions } from "../types/export";

export async function exportContent(options: ExportOptions): Promise<void> {
    const { data } = await apiClient.post("/export", options, {
        responseType: "blob",
    });

    // Trigger browser download
    const ext = { pdf: "pdf", latex: "tex", markdown: "md" }[options.format];
    const filename = `${options.title ?? "matopt-export"}.${ext}`;
    const url = URL.createObjectURL(data);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}
