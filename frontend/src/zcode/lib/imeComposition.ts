/* Vendored whole file from ZCode 29628c9acdb81b703bbd4080c207a0e7ce5e276e: packages/ui/src/lib/imeComposition.ts
 * Apache-2.0; original notices retained. */
export function isImeComposingKeyEvent(event: {
  compositionActive?: boolean;
  isComposing?: boolean;
  nativeEvent?: { isComposing?: boolean };
}): boolean {
  return Boolean(event.compositionActive || event.isComposing || event.nativeEvent?.isComposing);
}
