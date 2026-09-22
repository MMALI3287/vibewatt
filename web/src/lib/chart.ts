/**
 * Bar geometry for a chart `width` units wide with `count` bars. Past ~240 days
 * a 1-unit minimum width pushed the newest days off the edge (A-037), so bars
 * may be thinner than a unit and the gap goes before the newest day does.
 */
export function barLayout(count: number, width: number): { barWidth: number; gap: number } {
  if (count <= 0) return { barWidth: 0, gap: 0 };
  const gap = count > 120 ? 0 : 2;
  return { barWidth: (width - gap * (count - 1)) / count, gap };
}
