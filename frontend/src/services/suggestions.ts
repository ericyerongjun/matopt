/**
 * Suggestion service — fetch LLM-generated suggestions.
 */

import apiClient from "./api";
import type { SuggestionResponseBody } from "./types";

export async function fetchSuggestions(
    count = 4,
    options: { provider?: string | null; model?: string | null } = {}
): Promise<SuggestionResponseBody> {
    const { data } = await apiClient.get<SuggestionResponseBody>("/suggestions", {
        params: {
            count,
            provider: options.provider || undefined,
            model: options.model || undefined,
        },
    });
    return data;
}
