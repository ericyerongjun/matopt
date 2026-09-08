/** Shared API leftovers used by suggestions / follow-ups. */

export interface SuggestionResponseBody {
    suggestions: string[];
}

export interface FollowUpRequestBody {
    content: string;
    count?: number;
    provider?: string;
    model?: string;
}

export interface SuggestionResponseBody {
    suggestions: string[];
}

export interface FollowUpRequestBody {
    content: string;
    count?: number;
    provider?: string;
    model?: string;
}

export interface FollowUpResponseBody {
    followups: string[];
}

export interface OcrResponseBody {
    latex: string;
    confidence?: number;
    sympy_valid?: boolean;
    canonical_latex?: string | null;
}
