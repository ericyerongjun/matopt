import assert from "node:assert/strict";
import { test } from "node:test";
import { startSandbox } from "./server.mjs";

test("isolated runtime serves only two static assets and denies API, writes, and network permissions", async () => {
  const server = await startSandbox({ port: 0 });
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const document = await fetch(base);
    assert.equal(document.status, 200);
    assert.match(document.headers.get("content-security-policy"), /connect-src 'none'/);
    assert.match(document.headers.get("content-security-policy"), /sandbox allow-scripts/);
    assert.doesNotMatch(document.headers.get("content-security-policy"), /allow-same-origin/);
    assert.match(await document.text(), /runtime.js/);
    assert.equal((await fetch(`${base}/runtime.js`)).status, 200);
    assert.equal((await fetch(`${base}/api/conversations`)).status, 404);
    assert.equal((await fetch(`${base}/some-file.html`)).status, 404);
    assert.equal((await fetch(base, { method: "POST", body: "mutate" })).status, 405);
    assert.equal(document.headers.get("referrer-policy"), "no-referrer");
  } finally { await new Promise((resolve) => server.close(resolve)); }
});
