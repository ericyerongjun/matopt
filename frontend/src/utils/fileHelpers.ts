/**
 * File helper utilities for the frontend.
 */

/** Convert bytes to a human readable string. */
export function formatFileSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Get appropriate icon name for a MIME type. */
export function mimeIcon(mime: string): string {
    if (mime === "application/pdf") return "📄";
    if (mime.startsWith("image/")) return "🖼️";
    return "📁";
}

/** Check if a file is within the allowed size limit (in MB). */
export function isWithinSizeLimit(file: File, maxMb: number = 50): boolean {
    return file.size <= maxMb * 1024 * 1024;
}
