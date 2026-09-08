/**
 * @deprecated Prefer `exportConversation` from `./conversations`.
 */

import { exportConversation } from "./conversations";
import type { ExportOptions } from "../types/export";

export async function exportContent(options: ExportOptions): Promise<void> {
    if (!options.conversationId) {
        throw new Error("Export requires a conversation id");
    }
    await exportConversation(options.conversationId, options.format);
}
