type Series = { name: string; color: string; points: { x: number; y: number | null }[] };

const W = 600;
const H = 200;
const PAD = { left: 40, right: 10, top: 10, bottom: 22 };

export function LineChart({
  series,
  markers = [],
  formatX = (x) => x.toFixed(1),
  unit = "",
  onPick,
}: {
  series: Series[];
  markers?: { x: number; label: string }[];
  formatX?: (x: number) => string;
  unit?: string;
  onPick?: (x: number) => void;
}) {
  const xs = series.flatMap((s) => s.points.map((p) => p.x));
  const ys = series.flatMap((s) => s.points.flatMap((p) => (p.y === null ? [] : [p.y])));
  if (xs.length === 0 || ys.length === 0) return <p className="text-sm text-slate-500">No data yet.</p>;

  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  let [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  if (y0 === y1) [y0, y1] = [y0 - 1, y1 + 1];
  const sx = (x: number) => PAD.left + ((x - x0) / (x1 - x0 || 1)) * (W - PAD.left - PAD.right);
  const sy = (y: number) => H - PAD.bottom - ((y - y0) / (y1 - y0)) * (H - PAD.top - PAD.bottom);

  const paths = series.map((s) => {
    let d = "";
    let pen = false;
    for (const p of s.points) {
      if (p.y === null) {
        pen = false;
        continue;
      }
      d += `${pen ? "L" : "M"}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`;
      pen = true;
    }
    return { ...s, d, single: s.points.filter((p) => p.y !== null).length === 1 };
  });

  function pick(event: React.MouseEvent<SVGSVGElement>) {
    if (!onPick) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const px = ((event.clientX - rect.left) / rect.width) * W;
    onPick(x0 + ((px - PAD.left) / (W - PAD.left - PAD.right)) * (x1 - x0));
  }

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className={`w-full ${onPick ? "cursor-pointer" : ""}`} onClick={pick}>
        <line x1={PAD.left} x2={W - PAD.right} y1={H - PAD.bottom} y2={H - PAD.bottom} className="stroke-slate-300" />
        {[y0, (y0 + y1) / 2, y1].map((y) => (
          <text key={y} x={PAD.left - 4} y={sy(y) + 4} textAnchor="end" className="fill-slate-500 text-[10px]">
            {Math.round(y * 100) / 100}
            {unit}
          </text>
        ))}
        {[x0, x1].map((x, i) => (
          <text key={i} x={sx(x)} y={H - 6} textAnchor={i ? "end" : "start"} className="fill-slate-500 text-[10px]">
            {formatX(x)}
          </text>
        ))}
        {markers.map((m) => (
          <g key={m.label}>
            <line x1={sx(m.x)} x2={sx(m.x)} y1={PAD.top} y2={H - PAD.bottom} className="stroke-slate-400" strokeDasharray="4 3" />
            <text x={sx(m.x) + 3} y={PAD.top + 10} className="fill-slate-500 text-[10px]">
              {m.label}
            </text>
          </g>
        ))}
        {paths.map((p) =>
          p.single ? (
            p.points
              .filter((pt) => pt.y !== null)
              .map((pt) => <circle key={p.name} cx={sx(pt.x)} cy={sy(pt.y!)} r={4} fill={p.color} />)
          ) : (
            <path key={p.name} d={p.d} fill="none" stroke={p.color} strokeWidth={2} />
          ),
        )}
      </svg>
      <div className="mt-1 flex gap-4 text-xs text-slate-500">
        {series.map((s) => (
          <span key={s.name} className="flex items-center gap-1">
            <span className="inline-block h-2 w-3 rounded" style={{ background: s.color }} />
            {s.name}
          </span>
        ))}
      </div>
    </div>
  );
}
