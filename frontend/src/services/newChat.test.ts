import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { shouldRebindActiveId, shouldShowWelcome, shouldSteerExistingRun } from "./newChat.ts";

describe("shouldSteerExistingRun", () => {
    it("steers only while a live stream is still attached", () => {
        assert.equal(shouldSteerExistingRun({ hasActiveStream: true, detached: false }), true);
        assert.equal(shouldSteerExistingRun({ hasActiveStream: true, detached: true }), false);
        assert.equal(shouldSteerExistingRun({ hasActiveStream: false, detached: false }), false);
    });
});

describe("shouldRebindActiveId", () => {
    it("does not yank New Chat back onto the old SSE conversation", () => {
        assert.equal(
            shouldRebindActiveId({ detached: true, incomingConversationId: "c_old", activeId: null }),
            false
        );
        assert.equal(
            shouldRebindActiveId({ detached: false, incomingConversationId: "c_old", activeId: null }),
            true
        );
        assert.equal(
            shouldRebindActiveId({ detached: false, incomingConversationId: "c_old", activeId: "c_old" }),
            false
        );
    });
});

describe("shouldShowWelcome", () => {
    it("stays on the welcome screen after New Chat even if a stream is aborting", () => {
        assert.equal(
            shouldShowWelcome({ activeId: null, messageCount: 0, streaming: true, detached: true }),
            true
        );
        assert.equal(
            shouldShowWelcome({ activeId: null, messageCount: 0, streaming: true, detached: false }),
            false
        );
        assert.equal(
            shouldShowWelcome({ activeId: null, messageCount: 0, streaming: false, detached: false }),
            true
        );
    });
});
