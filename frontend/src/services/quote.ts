/** Quoted follow-ups, adapted from PR #7 without changing the chat protocol. */
export type QuoteSource = "user" | "assistant";
export interface QuoteRef { text: string; source: QuoteSource }
export interface QuoteRequest extends QuoteRef { id: string }
export interface QuotedMessage { quotes: QuoteRef[]; question: string }

export const MAX_QUOTES = 5;
export const MAX_QUOTE_CHARS = 6000;
const PREFIX = "Quoted conversation context (reference only):\n";

export function prepareQuote(quote: QuoteRef): QuoteRef {
    const text = quote.text.trim();
    const suffix = "\n[…truncated]";
    return { source: quote.source, text: text.length > MAX_QUOTE_CHARS
        ? text.slice(0, MAX_QUOTE_CHARS - suffix.length) + suffix : text };
}

/** JSON preserves literal tags, delimiters, mathematics, and question text. */
export function composeQuotedMessage(quotes: QuoteRef[], question: string): string {
    if (!quotes.length) return question;
    return PREFIX + JSON.stringify({ version: 1,
        quotes: quotes.slice(0, MAX_QUOTES).map(prepareQuote),
        question: question.trim() || "Explain the quoted passage.",
    });
}

/** Recognise only the complete envelope; malformed or ordinary prose stays visible. */
export function splitQuotedMessage(text: string): QuotedMessage | null {
    if (!text.startsWith(PREFIX)) return null;
    try {
        const value = JSON.parse(text.slice(PREFIX.length));
        if (!value || value.version !== 1 || typeof value.question !== "string"
            || Object.keys(value).sort().join(",") !== "question,quotes,version"
            || !Array.isArray(value.quotes) || !value.quotes.length || value.quotes.length > MAX_QUOTES) return null;
        for (const quote of value.quotes) {
            if (!quote || Object.keys(quote).sort().join(",") !== "source,text"
                || !["user", "assistant"].includes(quote.source) || typeof quote.text !== "string"
                || !quote.text.trim() || quote.text.length > MAX_QUOTE_CHARS) return null;
        }
        return { quotes: value.quotes, question: value.question };
    } catch { return null; }
}
