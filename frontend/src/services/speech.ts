/**
 * Voice service — local Whisper transcription and Kokoro speech on the backend.
 */

import apiClient from "./api.ts";

export interface SpeechLanguage { code: string; label: string; voice: string }
export interface SpeechStatus {
    enabled: boolean;
    transcription: boolean;
    synthesis: boolean;
    whisper_model: string;
    languages: SpeechLanguage[];
}
export interface Transcription { text: string; language: string; language_probability: number; duration: number }

export async function getSpeechStatus(): Promise<SpeechStatus> {
    const { data } = await apiClient.get<SpeechStatus>("/speech/status");
    return data;
}

export async function transcribe(audio: Blob, language?: string | null, signal?: AbortSignal): Promise<Transcription> {
    const form = new FormData();
    const extension = audio.type.includes("mp4") ? "m4a" : audio.type.includes("ogg") ? "ogg" : "webm";
    form.append("audio", audio, `voice.${extension}`);
    if (language) form.append("language", language);
    const { data } = await apiClient.post<Transcription>("/speech/transcribe", form, { signal, timeout: 180_000 });
    return data;
}

/** Spoken audio (WAV) for a reply; the server strips code, tables and display maths first. */
export async function synthesize(text: string, language?: string | null, signal?: AbortSignal): Promise<ArrayBuffer> {
    const response = await fetch("/api/speech/synthesize", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal,
        body: JSON.stringify({ text, language: language || undefined }),
    });
    if (!response.ok) {
        const detail = await response.json().then((body) => body?.detail).catch(() => null);
        throw new Error(typeof detail === "string" ? detail : `Speech failed (${response.status})`);
    }
    return response.arrayBuffer();
}
