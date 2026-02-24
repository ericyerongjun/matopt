/** Shared API type definitions. */

export interface ChatRequestBody {
    messages: { role: string; content: string }[];
    stream?: boolean;
    use_sympy?: boolean;
    use_wolfram?: boolean;
}

export interface ChatResponseBody {
    id: string;
    content: string;
    tool_calls?: { name: string; arguments: Record<string, unknown>; result?: string }[];
    usage?: Record<string, number>;
}

export interface OcrResponseBody {
    latex: string;
    confidence: number;
    sympy_valid: boolean;
    canonical_latex: string | null;
}

export interface MathRequestBody {
    latex: string;
    variable?: string;
    order?: number;
    lower?: string;
    upper?: string;
    substitutions?: Record<string, number>;
}

export interface MathResponseBody {
    success: boolean;
    result: string;
    error?: string;
}

export interface SuggestionResponseBody {
    suggestions: string[];
}

export interface FollowUpRequestBody {
    content: string;
    count?: number;
}

export interface FollowUpResponseBody {
    followups: string[];
}
