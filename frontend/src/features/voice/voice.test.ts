import assert from "node:assert/strict";
import { test } from "node:test";
import { chunkForSpeech, visemeFrom } from "./playback.ts";
import { detectLanguage, SILENT } from "./voiceStore.ts";

test("reply language is detected from script when the user has not chosen one", () => {
  assert.equal(detectLanguage("The gradient is 2x."), "en");
  assert.equal(detectLanguage("梯度是 2x。"), "zh");
  assert.equal(detectLanguage("勾配は2xです。"), "ja");
  assert.equal(detectLanguage("ग्रेडिएंट 2x है।"), "hi");
});

test("mouth shapes follow loudness and spectral balance", () => {
  assert.deepEqual(visemeFrom({ low: 1, mid: 1, high: 1 }, 0), SILENT);
  const open = visemeFrom({ low: 1, mid: 8, high: 1 }, 0.8);
  const rounded = visemeFrom({ low: 8, mid: 1, high: 1 }, 0.8);
  const spread = visemeFrom({ low: 1, mid: 1, high: 8 }, 0.8);
  assert.ok(open.aa > open.oh && open.aa > open.ih);
  assert.ok(rounded.oh > rounded.aa && rounded.oh > rounded.ih);
  assert.ok(spread.ih > spread.aa && spread.ih > spread.oh);
});

test("replies are spoken in short pieces, with code and display maths kept whole", () => {
  const reply = "First sentence. Second sentence is here.\n\n```python\nprint(1)\n\nprint(2)\n```\n\n$$a\n\nb$$\n\n" + "Long clause. ".repeat(40);
  const pieces = chunkForSpeech(reply, 60, 60);
  assert.equal(pieces[0], "First sentence. Second sentence is here.");
  assert.ok(pieces.includes("```python\nprint(1)\n\nprint(2)\n```"));
  assert.ok(pieces.includes("$$a\n\nb$$"));
  assert.ok(pieces.slice(3).every((piece) => piece.length <= 60 + "Long clause. ".length));
  assert.deepEqual(chunkForSpeech("梯度是一个向量。它指向增长最快的方向。", 8, 8), ["梯度是一个向量。", "它指向增长最快的方向。"]);
  const long = "A gradient of a function, which is differentiable, collects every partial derivative, and it points uphill; its size is the steepest rate.";
  const split = chunkForSpeech(long, 220, 40);
  assert.ok(split[0].length <= 60 && split.length > 1, "a long opening sentence breaks at clauses");
  assert.equal(split.join(" "), long);
  const tricky = "Take pi = 3.14, e.g. for a circle. Then stop.";
  assert.equal(chunkForSpeech(tricky).join(" "), tricky, "no text is ever dropped");
});
