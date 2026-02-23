/** Result of parsing a document via MinerU. */
export interface DocumentParseResult {
    markdown: string;
    latexBlocks: string[];
    metadata?: Record<string, unknown>;
}
