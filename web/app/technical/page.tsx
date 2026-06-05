"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search, RefreshCw, TrendingUp, TrendingDown, Minus, AlertTriangle } from "lucide-react";
import { stockTechnicalData } from "@/lib/api";
import type { TechnicalData } from "@/lib/api";

const VND = (n: number | null) => n == null ? "—" : n.toLocaleString("vi-VN") + " ₫";

const VERDICT_CONFIG = {
  BULLISH:      { label: "BULLISH",      color: "bg-emerald-500",  text: "text-emerald-600 dark:text-emerald-400",  ring: "ring-emerald-500" },
  MILD_BULLISH: { label: "MILD BULLISH", color: "bg-emerald-300",  text: "text-emerald-600 dark:text-emerald-400",  ring: "ring-emerald-300" },
  NEUTRAL:      { label: "NEUTRAL",      color: "bg-slate-400",    text: "text-slate-500",                          ring: "ring-slate-400" },
  MILD_BEARISH: { label: "MILD BEARISH", color: "bg-orange-400",   text: "text-orange-600 dark:text-orange-400",    ring: "ring-orange-400" },
  BEARISH:      { label: "BEARISH",      color: "bg-red-500",      text: "text-red-600 dark:text-red-400",          ring: "ring-red-500" },
} as const;

export default function TechnicalPage() {
  const [input, setInput]   = useState("FPT");
  const [ticker, setTicker] = useState("");

  const { data, isFetching, error } = useQuery({
    queryKey: ["technical-data", ticker],
    queryFn:  () => stockTechnicalData(ticker),
    enabled:  !!ticker,
  });

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = input.trim().toUpperCase();
    if (t) setTicker(t);
  };

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">Technical Analysis</h1>
        <p className="text-slate-500 mt-1 text-sm">
          MA · RSI · MACD · Bollinger Bands · ATR · Support/Resistance — from 365 days of price data.
        </p>
      </header>

      <form onSubmit={onSubmit} className="flex gap-3">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value.toUpperCase())}
          placeholder="FPT"
          className="w-28 px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-md bg-white dark:bg-slate-800 font-mono uppercase tracking-widest focus:outline-none focus:ring-2 focus:ring-slate-900"
        />
        <button
          type="submit"
          disabled={isFetching}
          className="px-4 py-2 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded-md font-medium hover:opacity-90 disabled:opacity-50 flex items-center gap-2"
        >
          {isFetching ? <RefreshCw size={16} className="animate-spin" /> : <Search size={16} />}
          Analyse
        </button>
      </form>

      {!ticker && <p className="text-slate-400 text-sm">Enter a ticker and press Analyse.</p>}
      {error  && <p className="text-red-600 text-sm">{String(error)}</p>}

      {data && <Dashboard data={data} />}
    </div>
  );
}

function Dashboard({ data }: { data: TechnicalData }) {
  const vc = VERDICT_CONFIG[data.verdict];
  return (
    <div className="space-y-5">

      {/* ── Signal header ─────────────────────────────────────────────── */}
      <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-5 flex flex-wrap items-center gap-6">
        <div className="flex items-center gap-4">
          <span className={`text-2xl font-bold px-5 py-2 rounded-lg text-white ${vc.color}`}>
            {vc.label}
          </span>
          <div>
            <p className="text-sm text-slate-500">Signal score</p>
            <SignalBar score={data.score} max={data.max_score} />
          </div>
        </div>
        <div className="ml-auto text-right">
          <p className="text-3xl font-bold tabular-nums">{VND(data.price)}</p>
          <p className="text-xs text-slate-400 mt-0.5">{data.ticker} · {data.n_days} trading days</p>
        </div>
      </div>

      {/* ── Main grid ─────────────────────────────────────────────────── */}
      <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
        <MaCard data={data} />
        <RsiCard rsi={data.rsi} />
        <MacdCard macd={data.macd} />
        <BbCard bb={data.bb} price={data.price} />
        <AtrCard atr={data.atr} atrPct={data.atr_pct} />
        <VolumeCard volume={data.volume} />
      </div>

      {/* ── Key levels ────────────────────────────────────────────────── */}
      <LevelsCard levels={data.levels} price={data.price} />
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-5">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-4">{title}</h3>
      {children}
    </section>
  );
}

function SignalBar({ score, max }: { score: number; max: number }) {
  // Map score from [-max, +max] to [0, 100]
  const pct = Math.round(((score + max) / (2 * max)) * 100);
  const color = score >= 2 ? "bg-emerald-500" : score >= 0 ? "bg-slate-400" : "bg-red-500";
  return (
    <div className="mt-1 flex items-center gap-2">
      <div className="w-32 h-2.5 rounded-full bg-slate-200 dark:bg-slate-700 overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-slate-500">{score > 0 ? "+" : ""}{score} / {max}</span>
    </div>
  );
}

function MaRow({ label, ma, price, pct }: { label: string; ma: number | null; price: number; pct: number | null }) {
  if (!ma || pct === null) return <p className="text-xs text-slate-400">{label}: no data</p>;
  const above = price > ma;
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-slate-100 dark:border-slate-800 last:border-0">
      <span className="text-sm font-medium w-16">{label}</span>
      <span className="text-sm tabular-nums text-slate-600 dark:text-slate-300">{VND(ma)}</span>
      <span className={`text-xs font-semibold flex items-center gap-0.5 ${above ? "text-emerald-600" : "text-red-500"}`}>
        {above ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
        {pct > 0 ? "+" : ""}{pct}%
      </span>
    </div>
  );
}

function MaCard({ data }: { data: TechnicalData }) {
  return (
    <Card title="Moving Averages">
      <MaRow label="MA 20"  ma={data.mas.ma20}  price={data.price} pct={data.mas.pct_from_ma20} />
      <MaRow label="MA 50"  ma={data.mas.ma50}  price={data.price} pct={data.mas.pct_from_ma50} />
      <MaRow label="MA 200" ma={data.mas.ma200} price={data.price} pct={data.mas.pct_from_ma200} />
    </Card>
  );
}

function RsiGauge({ value }: { value: number }) {
  const pct = Math.min(100, Math.max(0, value));
  const color = value > 70 ? "#ef4444" : value < 30 ? "#22c55e" : "#f59e0b";
  // SVG arc gauge
  const r = 36, cx = 50, cy = 50;
  const arcLen = Math.PI * r;
  const offset = arcLen * (1 - pct / 100);
  return (
    <div className="flex flex-col items-center">
      <svg viewBox="0 0 100 60" className="w-28">
        <path d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`} fill="none" stroke="#e2e8f0" strokeWidth="8" />
        <path
          d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`}
          fill="none" stroke={color} strokeWidth="8"
          strokeDasharray={arcLen} strokeDashoffset={offset}
          strokeLinecap="round"
        />
        <text x={cx} y={cy - 4} textAnchor="middle" fontSize="16" fontWeight="bold" fill={color}>{value.toFixed(1)}</text>
      </svg>
      <div className="flex justify-between w-28 text-[10px] text-slate-400 -mt-1">
        <span>0</span><span>OS=30</span><span>OB=70</span><span>100</span>
      </div>
    </div>
  );
}

function RsiCard({ rsi }: { rsi: number | null }) {
  const label = rsi == null ? "—" : rsi > 70 ? "Overbought" : rsi < 30 ? "Oversold" : "Neutral";
  const color = rsi == null ? "text-slate-400" : rsi > 70 ? "text-red-500" : rsi < 30 ? "text-emerald-500" : "text-amber-500";
  return (
    <Card title="RSI (14)">
      {rsi != null ? (
        <div className="flex flex-col items-center gap-2">
          <RsiGauge value={rsi} />
          <span className={`text-sm font-semibold ${color}`}>{label}</span>
        </div>
      ) : <p className="text-slate-400 text-sm">Insufficient data</p>}
    </Card>
  );
}

function MacdCard({ macd }: { macd: TechnicalData["macd"] }) {
  if (macd.macd === null) return <Card title="MACD (12/26/9)"><p className="text-slate-400 text-sm">Insufficient data</p></Card>;
  const bullish = (macd.macd ?? 0) > (macd.signal ?? 0);
  return (
    <Card title="MACD (12/26/9)">
      <div className="space-y-3">
        <div className="flex justify-between items-center">
          <span className={`text-lg font-bold tabular-nums ${bullish ? "text-emerald-600" : "text-red-500"}`}>
            {macd.macd! > 0 ? "+" : ""}{macd.macd?.toLocaleString()}
          </span>
          <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${bullish ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"}`}>
            {bullish ? "↑ Bullish cross" : "↓ Bearish cross"}
          </span>
        </div>
        {[
          { label: "MACD Line", val: macd.macd },
          { label: "Signal",    val: macd.signal },
          { label: "Histogram", val: macd.hist },
        ].map(({ label, val }) => (
          <div key={label} className="flex justify-between text-sm border-b border-slate-100 dark:border-slate-800 pb-1.5 last:border-0">
            <span className="text-slate-500">{label}</span>
            <span className={`font-mono tabular-nums ${(val ?? 0) > 0 ? "text-emerald-600" : "text-red-500"}`}>
              {val != null ? (val > 0 ? "+" : "") + val.toLocaleString() : "—"}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}

function BbCard({ bb, price }: { bb: TechnicalData["bb"]; price: number }) {
  if (!bb.upper) return <Card title="Bollinger Bands (20,2)"><p className="text-slate-400 text-sm">Insufficient data</p></Card>;
  const pct = bb.pct_b ?? 50;
  const zone = pct > 80 ? { label: "Near upper band", color: "text-red-500" }
             : pct < 20 ? { label: "Near lower band", color: "text-emerald-600" }
             : { label: "Mid-band", color: "text-slate-500" };
  const pricePos = Math.max(2, Math.min(96, pct));
  return (
    <Card title="Bollinger Bands (20,2)">
      <div className="space-y-3">
        <div className="relative h-6 rounded-full bg-gradient-to-r from-emerald-100 via-slate-100 to-red-100 dark:from-emerald-900/30 dark:via-slate-800 dark:to-red-900/30">
          <div className="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-slate-700 dark:bg-slate-200 shadow"
            style={{ left: `${pricePos}%`, transform: "translate(-50%, -50%)" }} />
        </div>
        <div className="flex justify-between text-[11px] text-slate-400">
          <span>{VND(bb.lower)}</span>
          <span>{VND(bb.mid)}</span>
          <span>{VND(bb.upper)}</span>
        </div>
        <div className="flex justify-between items-center">
          <span className="text-sm text-slate-500">%B position</span>
          <span className={`text-sm font-semibold ${zone.color}`}>{pct.toFixed(1)}% — {zone.label}</span>
        </div>
      </div>
    </Card>
  );
}

function AtrCard({ atr, atrPct }: { atr: number | null; atrPct: number | null }) {
  const vol = atrPct == null ? null : atrPct > 4 ? "High volatility" : atrPct > 2 ? "Moderate" : "Low volatility";
  const color = atrPct == null ? "" : atrPct > 4 ? "text-red-500" : atrPct > 2 ? "text-amber-500" : "text-emerald-600";
  return (
    <Card title="ATR — Volatility (14)">
      {atr != null ? (
        <div className="space-y-3">
          <p className="text-3xl font-bold tabular-nums">{VND(atr)}</p>
          <p className="text-sm text-slate-500">per day ≈ <span className={`font-semibold ${color}`}>{atrPct}%</span> of price</p>
          {vol && <p className={`text-sm font-medium ${color}`}>{vol}</p>}
          <p className="text-xs text-slate-400 border-t border-slate-100 dark:border-slate-800 pt-2">
            Suggested stop-loss: {VND(atr ? Math.round(atr * 2) : null)} below entry (2×ATR)
          </p>
        </div>
      ) : <p className="text-slate-400 text-sm">Insufficient data</p>}
    </Card>
  );
}

function VolumeCard({ volume }: { volume: TechnicalData["volume"] }) {
  const { last, avg20, ratio } = volume;
  const high = ratio > 1.2;
  const low  = ratio < 0.8;
  const color = high ? "text-emerald-600" : low ? "text-slate-400" : "text-slate-600 dark:text-slate-300";
  const label = high ? "Above average — conviction" : low ? "Below average — weak" : "Normal";
  const barPct = Math.min(200, Math.round(ratio * 100));
  return (
    <Card title="Volume">
      <div className="space-y-3">
        <div className="flex justify-between text-sm">
          <span className="text-slate-500">Last session</span>
          <span className="font-mono tabular-nums">{last.toLocaleString()}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-slate-500">20-day avg</span>
          <span className="font-mono tabular-nums">{avg20.toLocaleString()}</span>
        </div>
        <div>
          <div className="flex justify-between text-sm mb-1">
            <span className="text-slate-500">Relative volume</span>
            <span className={`font-semibold ${color}`}>{ratio}×</span>
          </div>
          <div className="h-2 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
            <div className={`h-full rounded-full ${high ? "bg-emerald-500" : low ? "bg-slate-400" : "bg-blue-400"}`}
              style={{ width: `${Math.min(100, barPct / 2)}%` }} />
          </div>
        </div>
        <p className={`text-xs font-medium ${color}`}>{label}</p>
      </div>
    </Card>
  );
}

function LevelsCard({ levels, price }: { levels: TechnicalData["levels"]; price: number }) {
  const { resistance, pivot, support, w52_high, w52_low, pct_from_high, pct_from_low } = levels;
  const rangeMin = Math.min(w52_low, support);
  const rangeMax = Math.max(w52_high, resistance);
  const toPos = (v: number) => Math.round(((v - rangeMin) / (rangeMax - rangeMin)) * 100);

  const markers = [
    { label: "52W Low",    value: w52_low,    pos: toPos(w52_low),    color: "bg-red-300 dark:bg-red-800" },
    { label: "Support",    value: support,    pos: toPos(support),    color: "bg-emerald-400 dark:bg-emerald-700" },
    { label: "Pivot",      value: pivot,      pos: toPos(pivot),      color: "bg-slate-400" },
    { label: "Resistance", value: resistance, pos: toPos(resistance), color: "bg-orange-400 dark:bg-orange-700" },
    { label: "52W High",   value: w52_high,   pos: toPos(w52_high),   color: "bg-red-500 dark:bg-red-700" },
  ];

  const pricePos = toPos(price);

  return (
    <section className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-5">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-5">Key Price Levels</h3>

      {/* Range bar */}
      <div className="relative h-4 rounded-full bg-slate-100 dark:bg-slate-800 mb-8">
        {markers.map(m => (
          <div key={m.label}
            className={`absolute top-0 h-4 w-1 rounded-sm ${m.color}`}
            style={{ left: `${m.pos}%`, transform: "translateX(-50%)" }}
          />
        ))}
        {/* Price dot */}
        <div className="absolute top-1/2 w-3 h-3 rounded-full bg-slate-900 dark:bg-white shadow ring-2 ring-white dark:ring-slate-900"
          style={{ left: `${pricePos}%`, transform: "translate(-50%, -50%)" }}
        />
        {/* Labels below */}
        {markers.map(m => (
          <div key={m.label + "-label"}
            className="absolute top-6 text-[10px] text-slate-400 whitespace-nowrap"
            style={{ left: `${m.pos}%`, transform: "translateX(-50%)" }}
          >
            {m.label}
          </div>
        ))}
      </div>

      {/* Table */}
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3 mt-4">
        {[
          { label: "Resistance (60d)", value: VND(resistance), sub: "" },
          { label: "Pivot",            value: VND(pivot),      sub: "" },
          { label: "Support (60d)",    value: VND(support),    sub: "" },
          { label: "52W High", value: VND(w52_high), sub: `${pct_from_high}% from current` },
          { label: "52W Low",  value: VND(w52_low),  sub: `+${pct_from_low}% from current` },
        ].map(({ label, value, sub }) => (
          <div key={label} className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
            <p className="text-xs text-slate-400 mb-0.5">{label}</p>
            <p className="font-semibold tabular-nums text-sm">{value}</p>
            {sub && <p className="text-xs text-slate-400 mt-0.5">{sub}</p>}
          </div>
        ))}
      </div>
    </section>
  );
}
