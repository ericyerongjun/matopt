import type { Server } from "node:http";
export const SANDBOX_CSP: string;
export function startSandbox(options?: { host?: string; port?: number }): Promise<Server>;
