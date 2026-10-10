/* Vendored whole file from ZCode 29628c9acdb81b703bbd4080c207a0e7ce5e276e: packages/shared/src/zcode-protocol-v4/submission.ts
 * Apache-2.0; original notices retained. */
import { z } from "zod";

/** Composer 可以显式提交的 Agent mode；auto 是 Runtime 内部状态，不进入用户 Submission。 */
export const submissionModeSchema = z.enum(["build", "edit", "plan", "yolo"]);
export type SubmissionMode = z.infer<typeof submissionModeSchema>;
