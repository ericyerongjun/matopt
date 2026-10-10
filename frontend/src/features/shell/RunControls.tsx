import { Loader2, Pause, Play, Square } from "lucide-react";

export function RunControls({ paused, pauseRequested, reconnecting, status, onPause, onResume, onStop }: {
  paused: boolean; pauseRequested: boolean; reconnecting: boolean; status: string | null;
  onPause: () => void; onResume: () => void; onStop: () => void;
}) {
  const pending = pauseRequested || Boolean(status && /requested|stopping|pausing/i.test(status));
  const label = reconnecting ? "Reconnecting to task…" : status || (paused ? "Paused. Ready when you are." : "Working. You can add an instruction.");
  return <div className="matopt-run-controls" aria-label="Task controls">
    <p role="status">{reconnecting ? <Loader2 className="matopt-work-spinning" aria-hidden="true" /> : <span className={`matopt-run-dot${paused ? ' is-paused' : ''}`} aria-hidden="true" />}{label}</p>
    <div className="matopt-run-buttons">
      <button type="button" disabled={pending || reconnecting} onClick={paused ? onResume : onPause}>{paused ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}{pauseRequested ? "Pausing…" : paused ? "Resume" : "Pause"}</button>
      <button type="button" disabled={Boolean(status && /stopping/i.test(status))} onClick={onStop}><Square aria-hidden="true" />Stop</button>
    </div>
  </div>;
}
