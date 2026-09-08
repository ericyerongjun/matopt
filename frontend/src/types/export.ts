export type ExportFormat = "markdown" | "ipynb";

export interface ExportOptions {
    format: ExportFormat;
    conversationId: string;
    title?: string;
    content?: string;
}
