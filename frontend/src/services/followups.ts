/**
 * Follow-up service — fetch follow-up questions for the latest assistant reply.
 */

import apiClient from "./api";
import type { FollowUpRequestBody, FollowUpResponseBody } from "./types";

export async function fetchFollowUps(
    body: FollowUpRequestBody
): Promise<FollowUpResponseBody> {
    const { data } = await apiClient.post<FollowUpResponseBody>(
        "/followups",
        body
    );
    return data;
}
