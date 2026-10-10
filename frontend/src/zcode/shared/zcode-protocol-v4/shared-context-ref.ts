/* Vendored whole file from ZCode 29628c9acdb81b703bbd4080c207a0e7ce5e276e: packages/shared/src/zcode-protocol-v4/shared-context-ref.ts
 * Apache-2.0; original notices retained. */
import { z } from "zod";

export const sharedContextRefSchema = z
  .object({
    kind: z.literal("shared_context_import"),
    context_id: z.string().trim().min(1),
  })
  .strict();

export type SharedContextRef = z.infer<typeof sharedContextRefSchema>;
