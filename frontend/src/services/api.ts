/**
 * Small fetch-based JSON client for the `/api` backend.
 * All service modules import this client. `defaults.adapter` is the transport
 * seam: tests replace it to answer requests without a network.
 */

export interface ApiRequestConfig {
    url: string;
    method: "get" | "post" | "patch" | "put" | "delete";
    /** JSON-encoded body, or FormData for uploads. */
    data?: string | FormData;
    params?: Record<string, string | number | boolean | null | undefined>;
    headers?: Record<string, string>;
    signal?: AbortSignal;
    timeout?: number;
}

export interface ApiResponse<T = unknown> {
    data: T;
    status: number;
    statusText?: string;
    headers?: Record<string, string>;
    config?: ApiRequestConfig;
}

export type ApiAdapter = (config: ApiRequestConfig) => Promise<ApiResponse>;
type Options = Pick<ApiRequestConfig, "params" | "headers" | "signal" | "timeout">;

const BASE_URL = "/api";
const DEFAULT_TIMEOUT = 120_000;

function withQuery(url: string, params?: ApiRequestConfig["params"]): string {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params ?? {})) {
        if (value !== undefined && value !== null) query.set(key, String(value));
    }
    const text = query.toString();
    return text ? `${url}${url.includes("?") ? "&" : "?"}${text}` : url;
}

const fetchAdapter: ApiAdapter = async (config) => {
    const signals = [AbortSignal.timeout(config.timeout ?? DEFAULT_TIMEOUT), ...(config.signal ? [config.signal] : [])];
    const headers: Record<string, string> = { ...config.headers };
    if (typeof config.data === "string") headers["Content-Type"] ??= "application/json";
    const response = await fetch(BASE_URL + withQuery(config.url, config.params), {
        method: config.method.toUpperCase(), body: config.data, headers, signal: AbortSignal.any(signals),
    });
    const text = await response.text();
    let data: unknown = text;
    try { data = text ? JSON.parse(text) : null; } catch { /* Non-JSON bodies stay text. */ }
    if (!response.ok) {
        const detail = (data && typeof data === "object" && "detail" in data) ? (data as { detail: unknown }).detail : null;
        throw Object.assign(new Error(typeof detail === "string" ? detail : `${response.status} ${response.statusText}`.trim()), { status: response.status });
    }
    return { data, status: response.status, statusText: response.statusText, config };
};

async function request<T>(config: ApiRequestConfig): Promise<ApiResponse<T>> {
    try {
        return await apiClient.defaults.adapter(config) as ApiResponse<T>;
    } catch (error) {
        // Selection changes deliberately abort requests; callers handle those quietly.
        if (!(error instanceof DOMException && (error.name === "AbortError" || error.name === "TimeoutError"))) {
            console.error("[API]", error instanceof Error ? error.message : error);
        }
        throw error;
    }
}

function body(data: unknown): string | FormData | undefined {
    return data === undefined ? undefined : data instanceof FormData ? data : JSON.stringify(data);
}

const apiClient = {
    defaults: { adapter: fetchAdapter as ApiAdapter },
    get: <T = unknown>(url: string, options: Options = {}) => request<T>({ ...options, url, method: "get" }),
    delete: <T = unknown>(url: string, options: Options = {}) => request<T>({ ...options, url, method: "delete" }),
    post: <T = unknown>(url: string, data?: unknown, options: Options = {}) =>
        request<T>({ ...options, url, method: "post", data: body(data) }),
    patch: <T = unknown>(url: string, data?: unknown, options: Options = {}) =>
        request<T>({ ...options, url, method: "patch", data: body(data) }),
    put: <T = unknown>(url: string, data?: unknown, options: Options = {}) =>
        request<T>({ ...options, url, method: "put", data: body(data) }),
};

/** HTTP status attached to a failed request, when the server answered. */
export function errorStatus(error: unknown): number | undefined {
    const status = (error as { status?: unknown } | null)?.status;
    return typeof status === "number" ? status : undefined;
}

/** True when the endpoint does not exist on this server or the feature behind it is off. */
export function isUnavailable(error: unknown): boolean {
    const status = errorStatus(error);
    return status === 404 || status === 405 || status === 501 || status === 503;
}

/** A short, user-facing message for a failed request. */
export function errorMessage(error: unknown, fallback: string): string {
    return error instanceof Error && error.message ? error.message : fallback;
}

export default apiClient;
