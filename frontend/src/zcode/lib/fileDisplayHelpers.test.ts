import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { resolveFileDisplayDescriptor } from "./fileDisplay.tsx";

test("CSV and TSV descriptors use the shipped native table icon", () => {
  for (const file of ["data.csv", "results/data.CSV", "results/table.tsv"]) {
    const descriptor = resolveFileDisplayDescriptor(file);
    assert.equal(descriptor.fileIcon, "table");
    assert.equal(descriptor.fileIconSrc, "/material-icons/table.svg");
    const icon = readFileSync(new URL("../../../public" + descriptor.fileIconSrc, import.meta.url), "utf8");
    assert.match(icon, /<svg\b/);
  }
});
