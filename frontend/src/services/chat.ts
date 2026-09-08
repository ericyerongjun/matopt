/**
 * @deprecated Chat is streamed via `streamChat` in `./stream`.
 */

export async function sendChatMessage(): Promise<never> {
    throw new Error("Use streamChat() / POST /api/chat/stream");
}
