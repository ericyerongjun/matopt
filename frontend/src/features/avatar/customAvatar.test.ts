import assert from "node:assert/strict";
import { test } from "node:test";
import { loadCustomAvatar, saveCustomAvatar } from "./customAvatar.ts";

test("only VRM (glTF binary) files are accepted as a custom avatar", async () => {
  await assert.rejects(saveCustomAvatar(new File(["hello"], "face.png")), /\.vrm/);
  await assert.rejects(saveCustomAvatar(new File(["notgltf"], "fake.vrm")), /not a VRM/);
  await saveCustomAvatar(new File(["glTF\x02\x00\x00\x00"], "ok.vrm"));  // Storage may be unavailable; still accepted.
});

test("without browser storage the default avatar is used", async () => {
  assert.equal(await loadCustomAvatar(), null);
});
