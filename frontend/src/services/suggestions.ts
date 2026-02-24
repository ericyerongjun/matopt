/**
 * Suggestion service — fetch LLM-generated suggestions.
 */

import apiClient from "./api";
import type { SuggestionResponseBody } from "./types";

export async function fetchSuggestions(
    count = 4
): Promise<SuggestionResponseBody> {
    const { data } = await apiClient.get<SuggestionResponseBody>("/suggestions", {
        params: { count },
    });
    return data;
}
