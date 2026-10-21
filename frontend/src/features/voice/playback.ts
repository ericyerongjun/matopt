/**
 * Speaks a reply and drives the avatar's mouth from the audio itself.
 *
 * Each frame, loudness opens the jaw and the balance of low, mid and high
 * frequencies picks between rounded (o/u), open (a) and spread (i/e) mouth
 * shapes. It is not phoneme-accurate, but it tracks the voice convincingly.
 */
import { synthesize } from "../../services/speech.ts";
import { detectLanguage, SILENT, useVoiceStore, type Viseme } from "./voiceStore.ts";

let context: AudioContext | null = null;
let current: { source: AudioBufferSourceNode | null; frame: number; abort: AbortController } | null = null;
/** Slowly decaying peak loudness of the reply being spoken. */
let peak = 0.05;

function audioContext(): AudioContext {
    context ??= new AudioContext();
    if (context.state === "suspended") void context.resume();
    return context;
}

export function stopSpeaking(): void {
    if (!current) return;
    current.abort.abort();
    cancelAnimationFrame(current.frame);
    try { current.source?.stop(); } catch { /* Already ended. */ }
    current = null;
    useVoiceStore.getState().setSpeech(false);
}

/** `loudness` is relative to the reply's recent peak (0-1), so every voice gets the same mouth range. */
export function visemeFrom(bands: { low: number; mid: number; high: number }, loudness: number): Viseme {
    const open = Math.min(1, loudness);
    if (open < 0.06) return SILENT;
    const peak = Math.max(bands.low, bands.mid, bands.high) || 1;
    const low = bands.low / peak, mid = bands.mid / peak, high = bands.high / peak;
    return { aa: open * mid * 0.85, oh: open * low * 0.7, ou: open * low * 0.35, ih: open * high * 0.6, ee: open * high * 0.4 };
}

/**
 * Split a Markdown reply into short spoken pieces so the first one plays
 * within about a second. Fenced code and display equations stay whole (the
 * server summarises them); long paragraphs break at sentence ends.
 */
export function chunkForSpeech(text: string, target = 220, first = 90): string[] {
    const blocks = text.split(/(```[\s\S]*?```|\$\$[\s\S]*?\$\$)/);
    const pieces: string[] = [];
    // The opening piece is short so speech starts quickly; later pieces synthesize while earlier ones play.
    const limit = () => pieces.length ? target : first;
    const clauses = (sentence: string) => sentence.length <= limit() ? [sentence]
        : sentence.split(/(?<=[,;:，；：])\s*/).filter((clause) => clause.trim());
    for (const block of blocks) {
        if (!block.trim()) continue;
        if (block.startsWith("```") || block.startsWith("$$")) { pieces.push(block); continue; }
        for (const paragraph of block.split(/\n{2,}/)) {
            // Split after CJK terminators, or after .!? that end a sentence; never drops text ("3.14", "e.g.").
            const sentences = paragraph.split(/(?<=[。！？])|(?<=[.!?])\s+/).filter((sentence) => sentence.trim());
            let current = "";
            for (const sentence of sentences) {
                for (const clause of clauses(sentence)) {
                    if (current && (current + clause).length > limit()) { pieces.push(current.trim()); current = ""; }
                    current += (current && !/[。！？，；：]$/.test(current) ? " " : "") + clause;
                }
            }
            if (current.trim()) pieces.push(current.trim());
        }
    }
    return pieces;
}

/** Synthesize and play a reply piece by piece; resolves when playback ends or is interrupted. */
export async function speak(text: string, language?: string | null): Promise<void> {
    stopSpeaking();
    const abort = new AbortController();
    const store = useVoiceStore.getState();
    const lang = language || store.language || detectLanguage(text);
    const pieces = chunkForSpeech(text);
    peak = 0.05;
    const fetchPiece = (index: number) => index < pieces.length
        ? synthesize(pieces[index], lang, abort.signal).catch((error) => { if (abort.signal.aborted) return null; throw error; })
        : Promise.resolve(null);
    let next = fetchPiece(0);
    current = { source: null, frame: 0, abort };
    for (let index = 0; index < pieces.length && !abort.signal.aborted; index += 1) {
        let wav: ArrayBuffer | null;
        try {
            wav = await next;
        } catch (error) {
            if (index === 0) { if (current?.abort === abort) current = null; throw error; }
            break;  // A later piece failed: keep what was already spoken.
        }
        next = fetchPiece(index + 1);  // Synthesize ahead while this piece plays.
        if (!wav || abort.signal.aborted) break;
        await playPiece(wav, abort);
    }
    if (current?.abort === abort) { cancelAnimationFrame(current.frame); current = null; }
    useVoiceStore.getState().setSpeech(false);
}

async function playPiece(wav: ArrayBuffer, abort: AbortController): Promise<void> {
    const ctx = audioContext();
    const buffer = await ctx.decodeAudioData(wav);
    if (abort.signal.aborted) return;
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);
    analyser.connect(ctx.destination);
    const time = new Uint8Array(analyser.fftSize);
    const freq = new Uint8Array(analyser.frequencyBinCount);
    const binHz = ctx.sampleRate / analyser.fftSize;
    const band = (from: number, to: number) => {
        let sum = 0;
        for (let i = Math.floor(from / binHz); i < Math.min(freq.length, Math.ceil(to / binHz)); i += 1) sum += freq[i];
        return sum;
    };
    return new Promise((resolve) => {
        const tick = () => {
            analyser.getByteTimeDomainData(time);
            analyser.getByteFrequencyData(freq);
            let energy = 0;
            for (const value of time) energy += ((value - 128) / 128) ** 2;
            const loudness = Math.sqrt(energy / time.length);
            peak = Math.max(peak * 0.997, loudness);
            useVoiceStore.getState().setSpeech(true, visemeFrom({ low: band(150, 700), mid: band(700, 1800), high: band(1800, 5000) }, loudness / peak));
            if (current) current.frame = requestAnimationFrame(tick);
        };
        source.onended = () => {
            if (current?.source === source) cancelAnimationFrame(current.frame);
            resolve();
        };
        current = { source, frame: requestAnimationFrame(tick), abort };
        abort.signal.addEventListener("abort", () => resolve());
        source.start();
    });
}
