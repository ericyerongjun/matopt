/** Sends the browser to the backend's sign-in page when API_AUTH_TOKEN requires it. */
export async function redirectIfSignedOut(): Promise<void> {
    try {
        const response = await fetch("/api/auth/status");
        if (!response.ok) return;
        const status = (await response.json()) as { auth_required?: boolean; authenticated?: boolean };
        if (status.auth_required && !status.authenticated) window.location.replace("/api/auth/login");
    } catch {
        // An unreachable backend is reported by the app's own connection handling.
    }
}
