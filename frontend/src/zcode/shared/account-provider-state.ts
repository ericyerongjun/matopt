/* Vendored whole file from ZCode 29628c9acdb81b703bbd4080c207a0e7ce5e276e: packages/shared/src/account-provider-state.ts
 * Apache-2.0; original notices retained. */
import { z } from "zod";

// 原因随 Account State 发送，必须与严格协议共用枚举，避免整份快照被拒绝。
// credential-failed 表示凭据获取/校验失败，不能据此断言 OAuth 已失效。
export const accountProviderUnavailableReasonSchema = z.enum([
  "not-authenticated",
  "not-connected",
  "credential-failed",
  "not-entitled",
]);
export type AccountProviderUnavailableReason = z.infer<
  typeof accountProviderUnavailableReasonSchema
>;
