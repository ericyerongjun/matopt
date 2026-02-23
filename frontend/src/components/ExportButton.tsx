/**
 * ExportButton — dropdown to select export format and trigger download.
 */

import React, { useState } from "react";
import type { ExportFormat } from "../types/export";

interface Props {
    onExport: (format: ExportFormat) => void;
    disabled?: boolean;
}

const FORMATS: { value: ExportFormat; label: string }[] = [
    { value: "pdf", label: "PDF" },
    { value: "latex", label: "LaTeX" },
    { value: "markdown", label: "Markdown" },
];

export default function ExportButton({ onExport, disabled = false }: Props) {
    const [open, setOpen] = useState(false);

    return (
        <div className="export-button">
            <button
                className="export-button__trigger"
                onClick={() => setOpen(!open)}
                disabled={disabled}
            >
                Export ▾
            </button>
            {open && (
                <ul className="export-button__menu">
                    {FORMATS.map((f) => (
                        <li key={f.value}>
                            <button
                                onClick={() => {
                                    onExport(f.value);
                                    setOpen(false);
                                }}
                            >
                                {f.label}
                            </button>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}
