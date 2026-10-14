import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { TASK_STARTERS, taskStarterDraft } from "./taskStarters.ts";

describe("task starters", () => {
    it("offers the inference and optimisation examples", () => {
        assert.deepEqual(TASK_STARTERS.map((starter) => starter.label), [
            "Compare two groups",
            "Fit a regression",
            "Minimize a function",
        ]);
        assert.deepEqual(TASK_STARTERS.map((starter) => starter.skill), [
            "statistical-inference",
            "statistical-inference",
            "numerical-optimization",
        ]);
    });

    it("keeps a worked example with the selected skill and does not send it", () => {
        const [groups, regression, minimum] = TASK_STARTERS;
        assert.match(groups.text, /group,score/);
        assert.match(groups.text, /A,12/);
        assert.match(groups.text, /B,18/);
        assert.match(regression.text, /hours,score/);
        assert.match(minimum.text, /\(x - 1\)\^2/);
        assert.match(minimum.text, /more than one start/);
        for (const starter of TASK_STARTERS) {
            assert.deepEqual(taskStarterDraft(starter), { text: starter.text, skills: [starter.skill] });
        }
    });
});
