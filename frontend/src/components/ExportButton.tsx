/**
 * Export menu — markdown or Jupyter notebook from the conversation export API.
 */

import React, { useState } from "react";
import type { ExportFormat } from "../types/export";
import "./ExportButton.css";

interface Props {
    onExport: (format: ExportFormat) => void;
    disabled?: boolean;
}

const FORMATS: { value: ExportFormat; label: string }[] = [
    { value: "markdown", label: "Markdown" },
    { value: "ipynb", label: "Jupyter notebook" },
];

export default function ExportButton({ onExport, disabled = false }: Props) {
    const [open, setOpen] = useState(false);

    return (
        <div className="export-button">
            <button className="header-btn" onClick={() => setOpen(!open)} disabled={disabled} type="button">
                Export
            </button>
            {open && (
                <ul className="export-button__menu">
                    {FORMATS.map((f) => (
                        <li key={f.value}>
                            <button
                                type="button"
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
