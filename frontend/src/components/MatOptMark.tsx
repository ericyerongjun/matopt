const INPUTS = [[9, 12], [9, 20]] as const;
const HIDDEN = [[16, 8.5], [16, 16], [16, 23.5]] as const;
const OUTPUT = [23, 16] as const;
const EDGES = [
  ...INPUTS.flatMap(([x1, y1]) => HIDDEN.map(([x2, y2]) => `M${x1} ${y1}L${x2} ${y2}`)),
  ...HIDDEN.map(([x, y]) => `M${x} ${y}L${OUTPUT[0]} ${OUTPUT[1]}`),
].join("");

/** MatOpt's ink seal: a two-input, three-hidden, one-output network. */
export function MatOptMark({ size = 28, className }: { size?: number; className?: string }) {
  // Small renders get heavier strokes and nodes so the network still reads.
  const small = size <= 20;
  return <svg className={className} width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
    <circle cx="16" cy="16" r="16" fill="var(--mo-accent)" />
    <path d={EDGES} fill="none" stroke="var(--mo-on-accent)" strokeOpacity=".55" strokeWidth={small ? 1.8 : 1.1} />
    {[...INPUTS, ...HIDDEN].map(([cx, cy]) => <circle key={`${cx}:${cy}`} cx={cx} cy={cy} r={small ? 2.6 : 1.9} fill="var(--mo-on-accent)" />)}
    <circle cx={OUTPUT[0]} cy={OUTPUT[1]} r={small ? 3.2 : 2.4} fill="var(--mo-on-accent)" />
  </svg>;
}
