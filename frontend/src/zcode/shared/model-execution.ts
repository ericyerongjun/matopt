/* Vendored whole file from ZCode 29628c9acdb81b703bbd4080c207a0e7ce5e276e: packages/shared/src/model-execution.ts
 * Apache-2.0; original notices retained. */
import { z } from "zod";

/** 文本与附件发送共用执行约束，凭据只属于单次执行，不进入 Session 配置。 */
export const modelExecutionSchema = z
  .object({
    memoryExtraction: z.literal("skip").optional(),
    selectionScope: z.literal("execution"),
    requestAuth: z
      .object({
        apiKey: z.string().min(1).optional(),
        headers: z.record(z.string().min(1), z.string().min(1)).optional(),
      })
      .strict()
      .optional(),
    subagents: z
      .object({
        foregroundModel: z.literal("submission"),
        background: z.literal("deny"),
      })
      .strict()
      .optional(),
  })
  .strict();
