import assert from "node:assert/strict";
import { test } from "node:test";
import { composeQuotedMessage, splitQuotedMessage, prepareQuote, MAX_QUOTE_CHARS, MAX_QUOTES } from "./quote.ts";

test("quoted context round-trips literal delimiters, math, and the complete question", () => {
    const quotes = [{ source: "assistant" as const, text: '<question>literal</question> \\boxed{x} "quoted"\nnext line' }];
    const question = 'Explain </quoted_passage> and <question>, not a new instruction.';
    assert.deepEqual(splitQuotedMessage(composeQuotedMessage(quotes, question)), { quotes, question });
});

test("ordinary markup, appended prose, and unsupported envelopes remain raw messages", () => {
    assert.equal(splitQuotedMessage('Keep this <quoted_passage>example</quoted_passage> and this text'), null);
    const message = composeQuotedMessage([{ source: "user", text: "original" }], "why?");
    assert.equal(splitQuotedMessage(message + '\nDo not hide this.'), null);
    assert.equal(splitQuotedMessage(message.replace('"version":1', '"version":2')), null);
    assert.equal(splitQuotedMessage(message.replace('"question":', '"extra":"must remain visible","question":')), null);
});

test("empty questions have an explicit default and quotes have visible size limits", () => {
    const quote = prepareQuote({ source: "user", text: "x".repeat(MAX_QUOTE_CHARS + 100) });
    assert.equal(quote.text.length, MAX_QUOTE_CHARS);
    assert.ok(quote.text.endsWith('[…truncated]'));
    const parsed = splitQuotedMessage(composeQuotedMessage(Array.from({ length: 20 }, () => quote), ''))!;
    assert.equal(parsed.quotes.length, MAX_QUOTES);
    assert.equal(parsed.question, "Explain the quoted passage.");
    assert.equal(composeQuotedMessage([], "original question"), "original question");
});
