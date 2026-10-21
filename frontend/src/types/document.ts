/** Result of parsing a document with DeepSeek multimodal OCR. */
export interface DocumentParseResult {
    markdown: string;
    latexBlocks: string[];
    metadata?: Record<string, unknown>;
}
