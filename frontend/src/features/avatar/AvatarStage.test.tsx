import assert from "node:assert/strict";
import { test } from "node:test";
import "../../../test/jsdom-env.mjs";

const { releaseRenderer } = await import("./AvatarStage.tsx");

test("a removed avatar gives its WebGL context back to the browser", () => {
    const calls: string[] = [];
    releaseRenderer({ dispose: () => calls.push("dispose"), forceContextLoss: () => calls.push("forceContextLoss"),
        domElement: { remove: () => calls.push("remove") } as unknown as HTMLCanvasElement });
    assert.deepEqual(calls, ["dispose", "forceContextLoss", "remove"]);
});
