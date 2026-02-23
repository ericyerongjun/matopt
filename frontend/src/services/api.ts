/**
 * Base Axios instance with interceptors.
 * All service modules import this client.
 */

import axios from "axios";

const apiClient = axios.create({
    baseURL: "/api",
    timeout: 120_000,
    headers: { "Content-Type": "application/json" },
});

// ── Request interceptor (e.g. attach auth token) ────────────────────────
apiClient.interceptors.request.use(
    (config) => {
        // TODO: Add auth token if needed
        // const token = localStorage.getItem("token");
        // if (token) config.headers.Authorization = `Bearer ${token}`;
        return config;
    },
    (error) => Promise.reject(error)
);

// ── Response interceptor (unified error handling) ───────────────────────
apiClient.interceptors.response.use(
    (response) => response,
    (error) => {
        const msg =
            error.response?.data?.detail ??
            error.message ??
            "Unknown error";
        console.error("[API]", msg);
        return Promise.reject(new Error(msg));
    }
);

export default apiClient;
