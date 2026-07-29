"use client";

import { useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine, Cell,
} from "recharts";
import type { ForeignNetAnnualPoint } from "@/lib/api";

function fmtB(v: number) {
  const abs = Math.abs(v);
  const s = abs >= 1000 ? `${(v / 1000).toFixed(1)}T` : `${v.toFixed(0)}B`;
  return v >= 0 ? `+${s}` : s;
}

export function AnnualFlowChart({
  ticker,
  points,
  subtitle,
}: {
  ticker: string;
  points: ForeignNetAnnualPoint[];
  subtitle?: string;
}) {
  const [mode, setMode] = useState<"net" | "cum">("net");
  const dataKey = mode === "net" ? "net_val_b" : "cum_val_b";
  const label   = mode === "net" ? "Annual net" : "Cumulative";

  return (
    <section className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg p-5 shadow-sm space-y-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h3 className="font-semibold">{ticker} — Annual Net Foreign Flow (B VND)</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            {subtitle ?? "Source: VCI"} &nbsp;·&nbsp; Green = net buy &nbsp;·&nbsp; Red = net sell
          </p>
        </div>
        <div className="flex gap-1 shrink-0">
          {(["net", "cum"] as const).map(m => (
            <button key={m} onClick={() => setMode(m)}
              className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${
                mode === m
                  ? "bg-slate-800 dark:bg-slate-600 text-white"
                  : "text-slate-500 hover:text-slate-900 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-slate-700"
              }`}>
              {m === "net" ? "Annual" : "Cumulative"}
            </button>
          ))}
        </div>
      </div>

      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={points} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="year" tick={{ fontSize: 11 }} />
          <YAxis
            tickFormatter={(v: number) => {
              const abs = Math.abs(v);
              return abs >= 1000 ? `${(v / 1000).toFixed(0)}T` : `${v.toFixed(0)}B`;
            }}
            tick={{ fontSize: 11 }}
            width={52}
          />
          <ReferenceLine y={0} stroke="#94a3b8" />
          <Tooltip
            formatter={(value) => [fmtB(Number(value ?? 0)), label]}
            labelFormatter={(l) => `Year: ${l}`}
          />
          <Bar dataKey={dataKey} radius={[2, 2, 0, 0]}>
            {points.map((pt, i) => (
              <Cell key={i} fill={(mode === "net" ? pt.net_val_b : pt.cum_val_b) >= 0 ? "#10b981" : "#ef4444"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </section>
  );
}
