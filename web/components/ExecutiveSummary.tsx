"use client";

import { useState } from "react";
import { CheckCircle, XCircle, ChevronDown, ChevronRight, TrendingUp, TrendingDown, Minus, Shield, Zap } from "lucide-react";
import type { ExecutiveSummary } from "@/lib/api";

const MAX_SCORE = 5;

const SCORE_LABELS: Record<string, string> = {
  valuationPoint:       "Valuation",
  growthPoint:          "Growth",
  passPerformancePoint: "Performance",
  financialHealthPoint: "Financial Health",
  dividendPoint:        "Dividend",
};

const SCORE_COLORS = [
  "bg-red-500",
  "bg-orange-500",
  "bg-yellow-500",
  "bg-emerald-400",
  "bg-emerald-500",
];

const RISK_COLORS: Record<string, string> = {
  low:    "text-emerald-400",
  medium: "text-amber-400",
  high:   "text-red-400",
};

const RISK_BG: Record<string, string> = {
  low:    "bg-emerald-500/15 border-emerald-800",
  medium: "bg-amber-500/15 border-amber-800",
  high:   "bg-red-500/15 border-red-800",
};

function ScoreBar({ label, point }: { label: string; point: number }) {
  const color = SCORE_COLORS[Math.max(0, point - 1)] ?? "bg-slate-600";
  return (
    <div>
      <div className="flex justify-between items-center mb-1.5">
        <span className="text-xs text-slate-400">{label}</span>
        <span className={`text-xs font-bold ${SCORE_COLORS[Math.max(0, point - 1)]?.replace("bg-", "text-")}`}>
          {point}/{MAX_SCORE}
        </span>
      </div>
      <div className="h-2 rounded-full bg-slate-800 overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`}
          style={{ width: `${(point / MAX_SCORE) * 100}%` }} />
      </div>
    </div>
  );
}

function TaSignalBadge({ signal }: { signal: string }) {
  const s = signal.toLowerCase();
  if (s.includes("bullish")) return (
    <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-emerald-500/20 text-emerald-400 text-sm font-semibold border border-emerald-800">
      <TrendingUp size={14} /> Bullish
    </span>
  );
  if (s.includes("bearish")) return (
    <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-red-500/20 text-red-400 text-sm font-semibold border border-red-800">
      <TrendingDown size={14} /> Bearish
    </span>
  );
  return (
    <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-slate-700 text-slate-300 text-sm font-semibold border border-slate-600">
      <Minus size={14} /> Neutral
    </span>
  );
}

function CheckList({ items, showAll }: { items: { title: string; description: string; isPass: boolean }[]; showAll: boolean }) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const display = showAll ? items : items.slice(0, 5);

  return (
    <div className="space-y-1">
      {display.map((item, i) => (
        <div key={i} className="rounded-lg overflow-hidden">
          <button
            onClick={() => setExpanded(expanded === i ? null : i)}
            className="w-full flex items-start gap-2.5 px-3 py-2 text-left hover:bg-slate-800/60 transition-colors rounded-lg"
          >
            {item.isPass
              ? <CheckCircle size={15} className="text-emerald-400 mt-0.5 shrink-0" />
              : <XCircle size={15} className="text-red-400 mt-0.5 shrink-0" />}
            <div className="flex-1 min-w-0">
              <p className="text-sm text-slate-200 leading-snug">{item.title}</p>
            </div>
            {expanded === i
              ? <ChevronDown size={14} className="text-slate-500 mt-0.5 shrink-0" />
              : <ChevronRight size={14} className="text-slate-500 mt-0.5 shrink-0" />}
          </button>
          {expanded === i && (
            <div className="px-4 pb-2.5 pt-0 ml-7">
              <p className="text-xs text-slate-400 leading-relaxed">{item.description}</p>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export function ExecutiveSummarySection({ data }: { data: ExecutiveSummary }) {
  const [showAllRewards, setShowAllRewards] = useState(false);
  const [showAllRisks, setShowAllRisks] = useState(false);

  const scores = [
    { key: "valuationPoint",       point: data.valuationPoint?.point       ?? 0 },
    { key: "growthPoint",          point: data.growthPoint?.point          ?? 0 },
    { key: "passPerformancePoint", point: data.passPerformancePoint?.point  ?? 0 },
    { key: "financialHealthPoint", point: data.financialHealthPoint?.point  ?? 0 },
    { key: "dividendPoint",        point: data.dividendPoint?.point         ?? 0 },
  ];

  const totalScore = scores.reduce((s, x) => s + x.point, 0);
  const maxTotal   = scores.length * MAX_SCORE;

  return (
    <div className="space-y-4">
      {/* Header + TA signal */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h3 className="font-semibold text-slate-200">Executive Summary</h3>
          <p className="text-xs text-slate-500 mt-0.5">Powered by Simplize · refreshed hourly</p>
        </div>
        <div className="flex items-center gap-3">
          <TaSignalBadge signal={data.taSignal1d ?? ""} />
        </div>
      </div>

      {/* Score dimensions + risk metrics side by side */}
      <div className="grid md:grid-cols-2 gap-4">
        {/* Score bars */}
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-3">
          <div className="flex items-center justify-between mb-1">
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Quality Scores</p>
            <span className="text-xs font-bold text-slate-300">{totalScore}/{maxTotal}</span>
          </div>
          {scores.map(s => (
            <ScoreBar key={s.key} label={SCORE_LABELS[s.key]} point={s.point} />
          ))}
        </div>

        {/* Risk metrics */}
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-3">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Risk & Liquidity</p>

          <div className={`flex items-center justify-between rounded-lg px-3 py-2 border ${RISK_BG[data.overallRiskLevel] ?? "bg-slate-800 border-slate-700"}`}>
            <div className="flex items-center gap-2">
              <Shield size={14} className={RISK_COLORS[data.overallRiskLevel] ?? "text-slate-400"} />
              <span className="text-xs text-slate-300">Overall Risk</span>
            </div>
            <span className={`text-xs font-semibold capitalize ${RISK_COLORS[data.overallRiskLevel] ?? "text-slate-400"}`}>
              {data.overallRiskLevel}
            </span>
          </div>

          {[
            { label: "Downside Risk",  value: `${data.downsideRisk?.toFixed(1)}%`,   level: data.downsideRiskLevel },
            { label: "Liquidity Risk", value: data.liquidityRiskLevel?.toUpperCase(), level: data.liquidityRiskLevel },
          ].map(r => (
            <div key={r.label} className="flex justify-between text-sm">
              <span className="text-slate-500 text-xs">{r.label}</span>
              <span className={`text-xs font-semibold ${RISK_COLORS[r.level] ?? "text-slate-400"}`}>{r.value}</span>
            </div>
          ))}

          <div className="flex justify-between text-sm border-t border-slate-800 pt-2 mt-1">
            <span className="text-slate-500 text-xs">Sharpe Ratio (1Y)</span>
            <span className={`text-xs font-semibold tabular-nums ${(data.sharpeRatio ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>
              {data.sharpeRatio?.toFixed(2)}
            </span>
          </div>

          <div className="flex justify-between text-sm">
            <span className="text-slate-500 text-xs">Valuation Quality</span>
            <span className="text-xs font-semibold text-slate-300">
              {data.qualityValuation === "1" ? "Undervalued" : data.qualityValuation === "2" ? "Fair" : "Overvalued"}
            </span>
          </div>

          <p className="text-[10px] text-slate-600 leading-relaxed pt-1 border-t border-slate-800">
            {data.liquidityMsg?.slice(0, 120)}…
          </p>
        </div>
      </div>

      {/* Rewards + Risks */}
      <div className="grid md:grid-cols-2 gap-4">
        {/* Rewards */}
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <CheckCircle size={14} className="text-emerald-400" />
              <p className="text-xs font-semibold text-emerald-400 uppercase tracking-wider">
                Strengths ({data.rewards?.length ?? 0})
              </p>
            </div>
          </div>
          <CheckList items={data.rewards ?? []} showAll={showAllRewards} />
          {(data.rewards?.length ?? 0) > 5 && (
            <button
              onClick={() => setShowAllRewards(v => !v)}
              className="mt-2 text-xs text-slate-500 hover:text-slate-300 transition-colors w-full text-center py-1"
            >
              {showAllRewards ? "Show less" : `Show all ${data.rewards.length}`}
            </button>
          )}
        </div>

        {/* Risks */}
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <XCircle size={14} className="text-red-400" />
              <p className="text-xs font-semibold text-red-400 uppercase tracking-wider">
                Risks ({data.risks?.length ?? 0})
              </p>
            </div>
          </div>
          <CheckList items={data.risks ?? []} showAll={showAllRisks} />
          {(data.risks?.length ?? 0) > 5 && (
            <button
              onClick={() => setShowAllRisks(v => !v)}
              className="mt-2 text-xs text-slate-500 hover:text-slate-300 transition-colors w-full text-center py-1"
            >
              {showAllRisks ? "Show less" : `Show all ${data.risks.length}`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
