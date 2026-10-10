import type { UploadedFile, WorkspaceFile } from "../types/protocol";

export async function listFiles(conversationId: string, signal?: AbortSignal): Promise<WorkspaceFile[]> {
    const res = await fetch(`/api/conversations/${conversationId}/files`, { signal });
    if (!res.ok) throw new Error(await readError(res));
    const data = (await res.json()) as { files: WorkspaceFile[] };
    return data.files;
}

export async function uploadFiles(conversationId: string, files: File[]): Promise<UploadedFile[]> {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    const res = await fetch(`/api/conversations/${conversationId}/files`, {
        method: "POST",
        body: form,
    });
    if (!res.ok) throw new Error(await readError(res));
    const data = (await res.json()) as { files: UploadedFile[] };
    return data.files;
}

export async function deleteFile(conversationId: string, path: string): Promise<void> {
    const encodedPath = path.split("/").map(encodeURIComponent).join("/");
    const res = await fetch(`/api/conversations/${conversationId}/files/${encodedPath}`, {
        method: "DELETE",
    });
    if (!res.ok) throw new Error(await readError(res));
}

async function readError(res: Response): Promise<string> {
    try {
        const body = await res.json();
        const detail = body?.detail;
        if (typeof detail === "string") return detail;
        if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
        return JSON.stringify(body);
    } catch {
        return `${res.status} ${res.statusText}`;
    }
}
