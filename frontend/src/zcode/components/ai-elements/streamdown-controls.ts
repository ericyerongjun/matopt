/* Vendored whole file from ZCode 29628c9acdb81b703bbd4080c207a0e7ce5e276e: packages/ui/src/components/ai-elements/streamdown-controls.ts
 * Apache-2.0; original notices retained. MatOpt host adaptations are identified below. */
// 交互调整：表格本身已经能在消息流里完整浏览，再保留 fullscreen 入口会把阅读路径打断。
// 这里统一关闭表格放大，只保留复制/导出等轻量操作，消息正文和推理面板共用同一份配置。
export const STREAMDOWN_CONTROLS = {
  table: {
    fullscreen: false,
  },
} as const;
