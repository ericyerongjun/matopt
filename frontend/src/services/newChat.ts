/** Pure New Chat decisions so mid-stream abandons cannot steal the blank composer. */

export function shouldSteerExistingRun(input: {
    hasActiveStream: boolean;
    detached: boolean;
}): boolean {
    return input.hasActiveStream && !input.detached;
}

export function shouldRebindActiveId(input: {
    detached: boolean;
    incomingConversationId?: string | null;
    activeId: string | null;
}): boolean {
    if (input.detached) return false;
    return Boolean(input.incomingConversationId && !input.activeId);
}

export function shouldShowWelcome(input: {
    activeId: string | null;
    messageCount: number;
    streaming: boolean;
    detached: boolean;
}): boolean {
    if (input.activeId) return false;
    if (input.messageCount > 0) return false;
    if (input.streaming && !input.detached) return false;
    return true;
}
