/**
 * Empty canvas — a quiet start. Skills live in the composer (`/`).
 */

import "./WelcomeScreen.css";

export default function WelcomeScreen() {
    return (
        <div className="welcome">
            <h1 className="welcome__title">Where should we begin?</h1>
            <p className="welcome__hint">
                An agent for math, data, and documents. Type <kbd>/</kbd> for a skill, or turn on Web Search.
            </p>
        </div>
    );
}
