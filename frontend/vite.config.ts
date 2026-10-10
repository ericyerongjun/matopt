import { defineConfig, loadEnv, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";
import { startSandbox } from "./sandbox/server.mjs";

function isolatedUiRuntime(origin?: string): Plugin {
    return {
        name: "matopt-isolated-ui",
        async configureServer(server) {
            if (!origin) {
                const host = typeof server.config.server.host === "string" ? server.config.server.host : server.config.server.host ? "0.0.0.0" : "127.0.0.1";
                const runtime = await startSandbox({ host, port: 5174 });
                server.httpServer?.once("close", () => runtime.close());
            }
            server.middlewares.use((request, response, next) => {
                const source = origin ? new URL(origin).origin : `http://${new URL(`http://${request.headers.host || "127.0.0.1:5173"}`).hostname}:5174`;
                response.setHeader("Content-Security-Policy", `frame-src ${source} blob:; object-src 'none'; base-uri 'self'`);
                next();
            });
        },
    };
}

export default defineConfig(({ mode }) => {
    const env = loadEnv(mode, process.cwd(), "VITE_");
    const backend = process.env.MATOPT_API_ORIGIN || "http://localhost:8000";
    return {
    plugins: [react(), tailwindcss(), isolatedUiRuntime(env.VITE_UI_SANDBOX_ORIGIN)],
    resolve: { alias: { "@": fileURLToPath(new URL("./src/zcode", import.meta.url)), "@zcode/model-option-map": fileURLToPath(new URL("./src/zcode/model-option-map/index.ts", import.meta.url)), "@zcode/shared": fileURLToPath(new URL("./src/zcode/shared/index.ts", import.meta.url)) } },
    worker: { format: "es" },
    server: {
        port: 5173,
        proxy: {
            "/api": {
                target: backend,
                changeOrigin: true,
            },
            "/health": {
                target: backend,
                changeOrigin: true,
            },
        },
    },
    };
});
