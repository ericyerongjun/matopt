import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const SANDBOX_CSP = "default-src 'none'; script-src 'self' 'unsafe-inline' blob:; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; worker-src blob:; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; sandbox allow-scripts";
const assets = new Map([
  ["/", ["text/html; charset=utf-8", readFileSync(new URL("./index.html", import.meta.url))]],
  ["/runtime.js", ["application/javascript; charset=utf-8", readFileSync(new URL("./runtime.js", import.meta.url))]],
]);

/** Static routes only: never proxy API calls or expose workspace files. */
export function startSandbox({ host = "127.0.0.1", port = 5174 } = {}) {
  const server = createServer((request, response) => {
    const path = new URL(request.url || "/", "http://sandbox.invalid").pathname;
    const asset = assets.get(path);
    response.setHeader("Content-Security-Policy", SANDBOX_CSP);
    response.setHeader("X-Content-Type-Options", "nosniff");
    response.setHeader("Referrer-Policy", "no-referrer");
    response.setHeader("Cache-Control", "no-store");
    response.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=(), clipboard-read=(), clipboard-write=()");
    if (!["GET", "HEAD"].includes(request.method || "")) { response.writeHead(405); response.end(); return; }
    if (!asset) { response.writeHead(404); response.end("Not found"); return; }
    response.setHeader("Content-Type", asset[0]);
    response.writeHead(200); response.end(request.method === "HEAD" ? undefined : asset[1]);
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, () => { server.removeListener("error", reject); resolve(server); });
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const server = await startSandbox({ host: process.env.SANDBOX_HOST || "127.0.0.1", port: Number(process.env.SANDBOX_PORT || 5174) });
  console.log(`MatOpt isolated UI runtime listening on ${server.address().address}:${server.address().port}`);
}
