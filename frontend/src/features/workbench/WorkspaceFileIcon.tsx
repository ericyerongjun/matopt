import { File, FileCode2, FileImage, FileSpreadsheet, FileText } from "lucide-react";
import { fileExtension } from "./preview.ts";

/** A consistent line icon keeps file types recognizable without competing with filenames. */
export function WorkspaceFileIcon({ path, className }: { path: string; className?: string }) {
    const extension = fileExtension(path);
    const Icon = /^(png|jpe?g|webp|gif|svg|bmp|tiff?|ico|avif)$/.test(extension) ? FileImage
        : /^(csv|tsv|xlsx?|ods|parquet)$/.test(extension) ? FileSpreadsheet
        : /^(py|[cm]?[jt]sx?|json|jsonl|html?|css|scss|ya?ml|toml|xml|sh|sql|r|c|cpp|h|java|rs|go|tex)$/.test(extension) ? FileCode2
        : /^(pdf|md|markdown|txt|log|docx?|rtf)$/.test(extension) ? FileText : File;
    return <Icon size={20} strokeWidth={1.8} className={className} aria-hidden="true" />;
}
