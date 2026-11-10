import { Component, type ReactNode } from "react";

interface Props { fallback: ReactNode; children: ReactNode }

/** Keeps one failed view from blanking everything around it. */
export class ErrorBoundary extends Component<Props, { failed: boolean }> {
    override state = { failed: false };
    static getDerivedStateFromError() { return { failed: true }; }
    override componentDidCatch(error: unknown) { console.error("View failed to render", error); }
    override render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

export function AppCrashNotice() {
    return <div role="alert" style={{ display: "grid", placeItems: "center", minHeight: "100vh", padding: 16, textAlign: "center" }}>
        <div><p>Something went wrong displaying this page. Your chats are saved.</p>
            <button type="button" onClick={() => window.location.assign("/")}>Reload MatOpt</button></div>
    </div>;
}
