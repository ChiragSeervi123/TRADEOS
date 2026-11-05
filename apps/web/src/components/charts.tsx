"use client";

import { useMemo, useState } from "react";
import { cx } from "./ui";

/**
 * Single-series column chart (inline SVG).
 * Follows the dataviz spec: one hue (#2a78d6, validated), bars <= 24px with a 4px
 * rounded data-end and square baseline, solid hairline grid, clean ticks, a hover/
 * focus tooltip per bar (hit target = the full band), and a table view. One measure
 * per chart - never a dual axis. Single series, so the title names it (no legend box).
 */
const SERIES = "#2a78d6";
const GRID = "#e8eaee";
const TEXT_MUTED = "#667085";

export type BarDatum = { label: string; value: number; display?: string };

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  const f = v / exp;
  const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
  return nice * exp;
}

function compact(n: number): string {
  if (Math.abs(n) >= 1e7) return `${(n / 1e7).toFixed(n % 1e7 ? 1 : 0)}Cr`;
  if (Math.abs(n) >= 1e5) return `${(n / 1e5).toFixed(n % 1e5 ? 1 : 0)}L`;
  if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(n % 1e3 ? 1 : 0)}K`;
  return String(Math.round(n * 100) / 100);
}

function barPath(x: number, y: number, w: number, h: number, r: number) {
  if (h <= 0) return "";
  const rr = Math.min(r, h, w / 2);
  return `M${x},${y + h} V${y + rr} Q${x},${y} ${x + rr},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h} Z`;
}

export function BarChart({
  title,
  data,
  height = 200,
  valueLabel,
  formatValue = (v) => v.toLocaleString("en-IN"),
  tickEvery,
}: {
  title: string;
  data: BarDatum[];
  height?: number;
  valueLabel: string;
  formatValue?: (v: number) => string;
  tickEvery?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);
  const width = 640;
  const pad = { top: 12, right: 8, bottom: 26, left: 44 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const max = useMemo(() => niceMax(Math.max(0, ...data.map((d) => d.value))), [data]);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * max);
  const band = data.length ? innerW / data.length : innerW;
  const barW = Math.max(2, Math.min(24, band - 2));
  const labelStep = tickEvery ?? Math.max(1, Math.ceil(data.length / 8));
  const total = data.reduce((s, d) => s + d.value, 0);

  return (
    <figure className="relative">
      <div className="mb-1 flex items-center justify-between">
        <figcaption className="text-sm font-semibold text-ink">{title}</figcaption>
        <button type="button" onClick={() => setShowTable((s) => !s)} className="text-xs text-info hover:underline" aria-pressed={showTable}>
          {showTable ? "Show chart" : "Show table"}
        </button>
      </div>
      {showTable ? (
        <div className="max-h-64 overflow-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-surface-2 text-left text-xs text-muted">
                <th className="px-3 py-1.5 font-medium">Period</th>
                <th className="px-3 py-1.5 text-right font-medium">{valueLabel}</th>
              </tr>
            </thead>
            <tbody>
              {data.map((d) => (
                <tr key={d.label} className="border-t border-border">
                  <td className="px-3 py-1">{d.label}</td>
                  <td className="tabular px-3 py-1 text-right">{d.display ?? formatValue(d.value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : data.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted">No data for this period.</p>
      ) : (
        <div className="relative">
          <svg viewBox={`0 0 ${width} ${height}`} className="h-auto w-full" role="img" aria-label={`${title}. ${data.length} values, total ${formatValue(total)}.`}>
            {ticks.map((t) => {
              const y = pad.top + innerH - (t / max) * innerH;
              return (
                <g key={t}>
                  <line x1={pad.left} x2={width - pad.right} y1={y} y2={y} stroke={GRID} strokeWidth={1} />
                  <text x={pad.left - 6} y={y + 3.5} textAnchor="end" fontSize={10} fill={TEXT_MUTED}>
                    {compact(t)}
                  </text>
                </g>
              );
            })}
            {data.map((d, i) => {
              const h = (d.value / max) * innerH;
              const x = pad.left + i * band + (band - barW) / 2;
              const y = pad.top + innerH - h;
              return (
                <g key={d.label}>
                  <path d={barPath(x, y, barW, h, 4)} fill={SERIES} opacity={hover === null || hover === i ? 1 : 0.55} />
                  {i % labelStep === 0 && (
                    <text x={pad.left + i * band + band / 2} y={height - 8} textAnchor="middle" fontSize={10} fill={TEXT_MUTED}>
                      {d.label}
                    </text>
                  )}
                  <rect
                    x={pad.left + i * band}
                    y={pad.top}
                    width={band}
                    height={innerH}
                    fill="transparent"
                    tabIndex={0}
                    aria-label={`${d.label}: ${d.display ?? formatValue(d.value)}`}
                    onMouseEnter={() => setHover(i)}
                    onMouseLeave={() => setHover(null)}
                    onFocus={() => setHover(i)}
                    onBlur={() => setHover(null)}
                    className="outline-none"
                  />
                </g>
              );
            })}
          </svg>
          {hover !== null && data[hover] && (
            <div
              role="tooltip"
              className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 rounded-md border border-border bg-surface px-2.5 py-1.5 text-xs shadow-md"
              style={{ left: `${((pad.left + hover * band + band / 2) / width) * 100}%` }}
            >
              <p className="text-muted">{data[hover].label}</p>
              <p className="tabular font-semibold text-ink">
                <span className="mr-1.5 inline-block h-2 w-2 rounded-sm align-middle" style={{ background: SERIES }} />
                {data[hover].display ?? formatValue(data[hover].value)} <span className="font-normal text-muted">{valueLabel}</span>
              </p>
            </div>
          )}
        </div>
      )}
    </figure>
  );
}

/** Horizontal bars for a ranked/ordinal breakdown (e.g. ageing buckets, top products). */
export function HBarList({ title, data, formatValue, valueLabel }: { title: string; data: BarDatum[]; formatValue: (v: number) => string; valueLabel: string }) {
  const max = Math.max(0, ...data.map((d) => d.value)) || 1;
  const [showTable, setShowTable] = useState(false);
  return (
    <figure>
      <div className="mb-2 flex items-center justify-between">
        <figcaption className="text-sm font-semibold text-ink">{title}</figcaption>
        <button type="button" onClick={() => setShowTable((s) => !s)} className="text-xs text-info hover:underline" aria-pressed={showTable}>
          {showTable ? "Show chart" : "Show table"}
        </button>
      </div>
      {data.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted">No data.</p>
      ) : showTable ? (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th className="py-1 font-medium">Item</th>
              <th className="py-1 text-right font-medium">{valueLabel}</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.label} className="border-t border-border">
                <td className="py-1">{d.label}</td>
                <td className="tabular py-1 text-right">{d.display ?? formatValue(d.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <ul className="space-y-2">
          {data.map((d) => (
            <li key={d.label} title={`${d.label}: ${d.display ?? formatValue(d.value)}`}>
              <div className="mb-0.5 flex justify-between gap-2 text-xs">
                <span className="truncate text-ink-2">{d.label}</span>
                <span className="tabular shrink-0 font-medium text-ink">{d.display ?? formatValue(d.value)}</span>
              </div>
              <div className="h-2 w-full rounded-full bg-surface-2" aria-hidden>
                <div className={cx("h-2 rounded-full")} style={{ width: `${Math.max(d.value > 0 ? 2 : 0, (d.value / max) * 100)}%`, background: SERIES }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </figure>
  );
}
