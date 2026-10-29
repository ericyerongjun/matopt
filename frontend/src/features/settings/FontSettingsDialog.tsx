import { useState } from "react";
import { HardDrive, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button.js";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog.js";
import { Input } from "@/components/ui/input.js";
import {
    DEFAULT_FONT_PREFERENCES, FONT_ROLES, POPULAR_FONTS, canListLocalFonts, fontStack, listLocalFontFamilies,
    type FontPreferences, type FontRole,
} from "../../utils/fontPreferences.ts";
import "./font-settings.css";

const OTHER = "\u0000other";
const SAMPLES: Record<FontRole, string> = {
    reading: "The minimum of f(x, y) = x² + y² is attained at the origin.",
    interface: "New chat · Workspace · Check results",
    code: "res = minimize(f, x0, method=\"L-BFGS-B\")",
};
const ROLES = Object.keys(FONT_ROLES) as FontRole[];

/** Choose the reading, interface and code fonts; every change applies at once. */
export function FontSettingsDialog({ open, onOpenChange, value, onChange }: {
    open: boolean; onOpenChange: (open: boolean) => void; value: FontPreferences; onChange: (next: FontPreferences) => void;
}) {
    const [localFonts, setLocalFonts] = useState<string[] | null>(null);
    const [localStatus, setLocalStatus] = useState<string | null>(null);
    const [custom, setCustom] = useState<Partial<Record<FontRole, boolean>>>({});
    const listable = canListLocalFonts();

    const loadLocal = async () => {
        setLocalStatus("Waiting for permission…");
        try {
            const families = await listLocalFontFamilies();
            setLocalFonts(families);
            setLocalStatus(families.length ? `${families.length} fonts found on this computer.` : "No installed fonts were shared.");
        } catch {
            setLocalStatus("Font access was not allowed. You can still type the name of an installed font.");
        }
    };

    return <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="matopt-font-dialog">
            <DialogHeader>
                <DialogTitle>Fonts</DialogTitle>
                <DialogDescription>Pick a popular font, one installed on this computer, or type any installed font’s name.</DialogDescription>
            </DialogHeader>
            {ROLES.map((role) => {
                const meta = FONT_ROLES[role];
                const current = value[role];
                const known = current === null || POPULAR_FONTS[role].includes(current) || localFonts?.includes(current);
                const showCustom = custom[role] || !known;
                const id = `matopt-font-${role}`;
                const choose = (family: string | null) => onChange({ ...value, [role]: family });
                return <section key={role} className="matopt-font-role">
                    <label htmlFor={id}><strong>{meta.label}</strong><span>{meta.description}</span></label>
                    <select id={id} value={showCustom ? OTHER : current ?? ""} onChange={(event) => {
                        const next = event.target.value;
                        if (next === OTHER) { setCustom((state) => ({ ...state, [role]: true })); return; }
                        setCustom((state) => ({ ...state, [role]: false }));
                        choose(next || null);
                    }}>
                        <option value="">{meta.defaultFamily.replace(/ Variable$/, "")} (default)</option>
                        <optgroup label="Popular">{POPULAR_FONTS[role].map((family) => <option key={family} value={family}>{family === "system-ui" ? "System font" : family.replace(/ Variable$/, "")}</option>)}</optgroup>
                        {localFonts && localFonts.length > 0 && <optgroup label="On this computer">{localFonts.map((family) => <option key={family} value={family}>{family}</option>)}</optgroup>}
                        <option value={OTHER}>Other installed font…</option>
                    </select>
                    {showCustom && <Input aria-label={`${meta.label} font name`} placeholder="Font name, e.g. Source Han Serif" defaultValue={known ? "" : current ?? ""}
                        onChange={(event) => choose(event.target.value.trim() || null)} />}
                    <p className="matopt-font-sample" style={{ fontFamily: fontStack(role, current) }}>{SAMPLES[role]}</p>
                </section>;
            })}
            <div className="matopt-font-local">
                {listable
                    ? <Button type="button" variant="outline" size="sm" onClick={() => void loadLocal()}><HardDrive aria-hidden="true" />Show fonts on this computer</Button>
                    : <p>This browser can’t list installed fonts. Choose “Other installed font…” and type its name.</p>}
                {localStatus && <p role="status">{localStatus}</p>}
            </div>
            <DialogFooter>
                <Button type="button" variant="ghost" onClick={() => { setCustom({}); onChange({ ...DEFAULT_FONT_PREFERENCES }); }}><RotateCcw aria-hidden="true" />Reset to defaults</Button>
                <Button type="button" onClick={() => onOpenChange(false)}>Done</Button>
            </DialogFooter>
        </DialogContent>
    </Dialog>;
}
