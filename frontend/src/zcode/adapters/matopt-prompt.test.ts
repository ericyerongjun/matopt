import assert from "node:assert/strict";
import { test } from "node:test";
import type { CommandInfo } from "../../services/skills";
import { filterPromptCommands, promptFilesForUpload, resolvePromptSubmission } from "./matopt-prompt.ts";

const catalog: CommandInfo[] = [
  { kind: "skill", name: "lambda-eda", description: "Analyze tabular data", source: "lambda" },
  { kind: "tool", name: "read_file", description: "Read a workspace file", source: "matopt" },
  { kind: "skill", name: "sympy-compute", description: "Symbolic mathematics", source: "matopt" },
];

test("native prompt binding combines pinned and typed commands without duplicate tokens", () => {
  assert.deepEqual(resolvePromptSubmission("/lambda-eda /read-file inspect data", [catalog[0], catalog[1]], catalog), {
    text: "/lambda-eda /read-file inspect data", skills: ["lambda-eda"], tools: ["read_file"],
  });
  assert.equal(resolvePromptSubmission("  ", [], catalog), null);
  assert.deepEqual(resolvePromptSubmission("", [catalog[2]], catalog), {
    text: "/sympy-compute", skills: ["sympy-compute"], tools: [],
  });
});

test("unknown slash instructions remain literal and file tool aliases filter correctly", () => {
  assert.deepEqual(resolvePromptSubmission("/custom retain these words", [], catalog), {
    text: "/custom retain these words", skills: [], tools: [],
  });
  assert.deepEqual(filterPromptCommands(catalog, "read-file"), [catalog[1]]);
  assert.deepEqual(filterPromptCommands(catalog, "tabular"), [catalog[0]]);
});

test("native FileUIPart data URLs become exact named multipart uploads", async () => {
  const files = await promptFilesForUpload([{ type: "file", filename: "data.csv", mediaType: "text/csv", url: "data:text/csv;base64,eCx5CjEsMgo=" }]);
  assert.equal(files.length, 1);
  assert.equal(files[0].name, "data.csv");
  assert.equal(files[0].type, "text/csv");
  assert.equal(await files[0].text(), "x,y\n1,2\n");
  await assert.rejects(promptFilesForUpload([{ type: "file", filename: "external.csv", mediaType: "text/csv", url: "https://example.invalid/file" }]), /local files/);
});
