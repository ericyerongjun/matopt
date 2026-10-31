/** A pending tool approval, shown beside the run controls. Decisions go to the run. */
import { useState } from "react";
import { Check, ShieldAlert, X } from "lucide-react";
import type { ApprovalRequest } from "../../types/protocol.ts";
import { errorMessage } from "../../services/api.ts";
import { commandPresentation } from "../../components/commandPresentation.ts";
import "./runs.css";

const RISK: Record<ApprovalRequest["risk"], string> = { low: "Low risk", medium: "Medium risk", high: "High risk", critical: "Critical risk" };

export function ApprovalCard({ approval, onDecide }: {
    approval: ApprovalRequest; onDecide: (decision: "allow" | "deny") => Promise<void>;
}) {
    const [pending, setPending] = useState<"allow" | "deny" | null>(null);
    const [sent, setSent] = useState<"allow" | "deny" | null>(null);
    const [error, setError] = useState<string | null>(null);
    const titleId = `approval-${approval.id}-title`;
    const decide = async (decision: "allow" | "deny") => {
        setPending(decision); setError(null);
        try { await onDecide(decision); setSent(decision); }
        catch (failure) { setError(errorMessage(failure, "Could not send your decision. Try again.")); }
        finally { setPending(null); }
    };
    const tool = approval.tool ? commandPresentation({ name: approval.tool, kind: "tool" }).label : null;
    return <section className="matopt-approval" role="group" aria-labelledby={titleId} data-risk={approval.risk}>
        <div className="matopt-approval-head">
            <ShieldAlert aria-hidden="true" />
            <div className="matopt-approval-heading">
                <h3 id={titleId}>{approval.title}</h3>
                <p>{tool && <span>{tool}</span>}<span className="matopt-approval-risk">{RISK[approval.risk]}</span></p>
            </div>
        </div>
        {approval.summary && <p className="matopt-approval-summary">{approval.summary}</p>}
        {approval.reason && <p className="matopt-approval-reason"><span>Why approval is needed: </span>{approval.reason}</p>}
        {error && <p className="matopt-approval-error" role="alert">{error}</p>}
        <div className="matopt-approval-actions">
            {sent ? <p role="status">{sent === "allow" ? "Allowed. Continuing…" : "Denied. MatOpt will continue without it."}</p> : <>
                <button type="button" className="is-deny" disabled={pending !== null} onClick={() => void decide("deny")}><X aria-hidden="true" />{pending === "deny" ? "Denying…" : "Deny"}</button>
                <button type="button" className="is-allow" disabled={pending !== null} onClick={() => void decide("allow")}><Check aria-hidden="true" />{pending === "allow" ? "Allowing…" : "Allow"}</button>
            </>}
        </div>
    </section>;
}
