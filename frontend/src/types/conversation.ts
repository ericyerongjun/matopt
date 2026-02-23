import type { Message } from "./message";

/** A single conversation thread. */
export interface Conversation {
    id: string;
    title: string;
    messages: Message[];
    createdAt: number;
    updatedAt: number;
}
