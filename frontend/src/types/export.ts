/** Supported export formats. */
export type ExportFormat = "pdf" | "latex" | "markdown";

export interface ExportOptions {
    content: string;
    format: ExportFormat;
    title?: string;
    template?: string;
}
