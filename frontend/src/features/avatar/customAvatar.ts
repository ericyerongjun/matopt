/**
 * A user-chosen VRM avatar, kept in this browser (IndexedDB) so it survives
 * reloads without being uploaded anywhere.
 */
const DB = "matopt-avatar";
const STORE = "files";
const KEY = "custom";

function open(): Promise<IDBDatabase> {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB, 1);
        request.onupgradeneeded = () => request.result.createObjectStore(STORE);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

async function run<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
    const db = await open();
    try {
        return await new Promise<T>((resolve, reject) => {
            const request = action(db.transaction(STORE, mode).objectStore(STORE));
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    } finally {
        db.close();
    }
}

export async function loadCustomAvatar(): Promise<File | null> {
    try {
        const file = await run<File | undefined>("readonly", (store) => store.get(KEY));
        return file instanceof Blob ? file as File : null;
    } catch {
        return null;  // Private windows or blocked storage: fall back to the default avatar.
    }
}

export async function saveCustomAvatar(file: File): Promise<void> {
    if (!/\.vrm$/i.test(file.name)) throw new Error("Choose a .vrm avatar file.");
    if (file.size > 80 * 1024 * 1024) throw new Error("That avatar is larger than 80 MB.");
    const head = new Uint8Array(await file.slice(0, 4).arrayBuffer());
    if (String.fromCharCode(...head) !== "glTF") throw new Error("That file is not a VRM (glTF binary) model.");
    try { await run("readwrite", (store) => store.put(file, KEY)); } catch { /* Still usable for this session. */ }
}

export async function clearCustomAvatar(): Promise<void> {
    try { await run("readwrite", (store) => store.delete(KEY)); } catch { /* Nothing stored. */ }
}
