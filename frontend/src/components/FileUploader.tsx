/**
 * FileUploader — drag-and-drop file upload using react-dropzone.
 * Accepts PDFs and images.
 */

import React, { useCallback } from "react";
import { useDropzone } from "react-dropzone";

interface Props {
    onFile: (file: File) => void;
    accept?: Record<string, string[]>;
    disabled?: boolean;
}

const DEFAULT_ACCEPT: Record<string, string[]> = {
    "application/pdf": [".pdf"],
    "image/png": [".png"],
    "image/jpeg": [".jpg", ".jpeg"],
    "image/webp": [".webp"],
};

export default function FileUploader({
    onFile,
    accept = DEFAULT_ACCEPT,
    disabled = false,
}: Props) {
    const onDrop = useCallback(
        (accepted: File[]) => {
            if (accepted.length > 0) onFile(accepted[0]);
        },
        [onFile]
    );

    const { getRootProps, getInputProps, isDragActive } = useDropzone({
        onDrop,
        accept,
        maxFiles: 1,
        disabled,
    });

    return (
        <div
            {...getRootProps()}
            className={`file-uploader ${isDragActive ? "file-uploader--active" : ""}`}
        >
            <input {...getInputProps()} />
            {isDragActive ? (
                <p>Drop the file here…</p>
            ) : (
                <p>Drag & drop a PDF or image, or click to browse</p>
            )}
        </div>
    );
}
