"use client";

import { useState, useEffect, useRef, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { createChart, AreaSeries } from "lightweight-charts";
import { TrendingUp, TrendingDown, RefreshCw, Newspaper } from "lucide-react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine, Cell,
} from "recharts";
import { marketDashboardData, marketIndexChart, economyNews, marketForeignFlowChart, marketForeignNetAnnual } from "@/lib/api";
import { AnnualFlowChart } from "@/components/AnnualFlowChart";
import type { DashboardData, IndexInfo, Mover, IndexPrice, ForeignFlowResponse } from "@/lib/api";

const fmt    = (n: number | undefined, d = 2) => (n ?? 0).toLocaleString("vi-VN", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtVol = (n: number | undefined) => !n ? "—" : n >= 1e9 ? `${(n/1e9).toFixed(1)}B` : n >= 1e6 ? `${(n/1e6).toFixed(0)}M` : `${(n/1e3).toFixed(0)}K`;

function ChangeTag({ pct, pts }: { pct: number; pts?: number }) {
  const up = pct >= 0;
  return (
    <span className={`inline-flex items-center gap-0.5 text-xs font-semibold ${up ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
      {up ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
      {pts !== undefined && <span>{pts > 0 ? "+" : ""}{fmt(pts)}</span>}
      <span className={`px-1 py-0.5 rounded ${up ? "bg-emerald-100 dark:bg-emerald-500/20" : "bg-red-100 dark:bg-red-500/20"}`}>
        {pct > 0 ? "+" : ""}{fmt(pct)}%
      </span>
    </span>
  );
}

function IndexChart({ prices, days, onDaysChange }: { prices: IndexPrice[]; days: number; onDaysChange: (d: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || prices.length === 0) return;
    const el = ref.current;
    const isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const gridColor  = isDark ? "#1e293b" : "#f1f5f9";
    const textColor  = isDark ? "#94a3b8" : "#64748b";

    const chart = createChart(el, {
      width:  el.clientWidth,
      height: 280,
      layout: { background: { color: "transparent" }, textColor },
      grid:   { vertLines: { color: gridColor }, horzLines: { color: gridColor } },
      rightPriceScale: { borderColor: gridColor },
      timeScale:       { borderColor: gridColor, timeVisible: false },
      crosshair: { horzLine: { visible: true }, vertLine: { visible: true } },
      handleScale: { mouseWheel: true },
    });

    const last = prices[prices.length - 1]?.value ?? 0;
    const first = prices[0]?.value ?? 0;
    const up = last >= first;

    const area = chart.addSeries(AreaSeries, {
      lineColor:   up ? "#16a34a" : "#dc2626",
      topColor:    up ? "rgba(22,163,74,0.15)" : "rgba(220,38,38,0.15)",
      bottomColor: "rgba(0,0,0,0)",
      lineWidth: 2,
      priceLineVisible: false,
    });
    area.setData(prices.map(p => ({ time: p.date as unknown as import("lightweight-charts").Time, value: p.value })));
    chart.timeScale().fitContent();

    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    ro.observe(el);
    return () => { ro.disconnect(); chart.remove(); };
  }, [prices]);

  const RANGES = [{ label: "1M", days: 30 }, { label: "3M", days: 90 }, { label: "1Y", days: 365 }, { label: "5Y", days: 1825 }];
  return (
    <div>
      <div className="flex gap-1 mb-3">
        {RANGES.map(r => (
          <button key={r.days} onClick={() => onDaysChange(r.days)}
            className={`px-3 py-1 rounded text-xs font-medium transition-colors ${days === r.days ? "bg-slate-800 dark:bg-slate-600 text-white" : "text-slate-500 hover:text-slate-900 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-slate-700"}`}>
            {r.label}
          </button>
        ))}
      </div>
      <div ref={ref} />
    </div>
  );
}

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm ${className}`}>
      {children}
    </div>
  );
}

function IndexRow({ label, info, bold }: { label: string; info: IndexInfo; bold?: boolean }) {
  const up = info.change_pct >= 0;
  return (
    <div className={`flex items-center justify-between py-2.5 border-b border-slate-100 dark:border-slate-800 last:border-0 ${bold ? "text-slate-900 dark:text-white font-semibold" : "text-slate-600 dark:text-slate-300"}`}>
      <span className="text-sm">{label}</span>
      <div className="flex items-center gap-3 text-sm tabular-nums">
        <span className={`font-semibold ${bold ? "" : ""}`}>{fmt(info.value)}</span>
        <span className={`text-xs font-semibold px-1.5 py-0.5 rounded ${up ? "bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400" : "bg-red-100 dark:bg-red-500/20 text-red-700 dark:text-red-400"}`}>
          {info.change_pct > 0 ? "+" : ""}{fmt(info.change_pct)}%
        </span>
      </div>
    </div>
  );
}

function MoverTable({ title, movers, color, loading }: { title: string; movers: Mover[]; color: "green" | "red"; loading?: boolean }) {
  const c = color === "green";
  return (
    <Card className="p-4">
      <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-300 mb-3 flex items-center gap-2">
        {title}
        {loading && <RefreshCw size={11} className="animate-spin text-slate-400" />}
      </h3>
      <div>
        {movers.map(m => (
          <div key={m.ticker} className="flex items-center justify-between py-2 border-b border-slate-100 dark:border-slate-800 last:border-0">
            <span className="font-mono font-semibold text-sm text-slate-800 dark:text-slate-200 w-14">{m.ticker}</span>
            <span className="text-slate-400 text-xs tabular-nums">{(m.value / 1000).toFixed(1)}K</span>
            <div className="flex items-center gap-1.5 ml-auto">
              <div className="w-16 h-1.5 rounded-full bg-slate-100 dark:bg-slate-800">
                <div className={`h-full rounded-full ${c ? "bg-emerald-500" : "bg-red-500"}`}
                  style={{ width: `${Math.min(100, Math.abs(m.change_pct) * 10)}%` }} />
              </div>
              <span className={`text-xs font-semibold tabular-nums w-14 text-right ${c ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
                {m.change_pct > 0 ? "+" : ""}{fmt(m.change_pct)}%
              </span>
            </div>
          </div>
        ))}
        {movers.length === 0 && <p className="text-slate-400 text-xs py-2">No data</p>}
      </div>
    </Card>
  );
}

function TickerBar({ data }: { data: DashboardData }) {
  const items = [
    { label: "VN-Index", ...data.indices.vnindex },
    { label: "HNX",      ...data.indices.hnx },
    { label: "UPCOM",    ...data.indices.upcom },
  ];
  return (
    <div className="bg-slate-800 dark:bg-slate-900 border-b border-slate-700 dark:border-slate-800 px-6 py-2 flex gap-8 overflow-x-auto text-sm -mx-6 -mt-8 mb-6">
      {items.map(i => (
        <div key={i.label} className="flex items-center gap-2 shrink-0">
          <span className="text-slate-300 font-medium text-xs">{i.label}</span>
          <span className="text-white font-semibold tabular-nums text-sm">{fmt(i.value)}</span>
          <ChangeTag pct={i.change_pct} pts={i.change} />
        </div>
      ))}
    </div>
  );
}

function NewsStrip({ text }: { text: string }) {
  const lines = text.split("\n").filter(l => l.startsWith("|") && !l.startsWith("| Source") && !l.startsWith("|---"));
  const headlines = lines.slice(0, 6).map(l => {
    const cells = l.split("|").filter(Boolean).map(c => c.trim());
    const m = cells[1]?.match(/\[([^\]]+)\]\(([^)]+)\)/);
    return m ? { title: m[1], url: m[2] } : null;
  }).filter(Boolean) as { title: string; url: string }[];

  if (!headlines.length) return null;
  return (
    <Card className="p-4">
      <div className="flex items-center gap-2 mb-3">
        <Newspaper size={14} className="text-slate-400" />
        <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-300">Headlines</h3>
      </div>
      <ul className="space-y-2">
        {headlines.map((h, i) => (
          <li key={i}>
            <a href={h.url} target="_blank" rel="noreferrer"
              className="text-xs text-slate-500 hover:text-slate-800 dark:hover:text-slate-200 transition-colors line-clamp-2 leading-relaxed">
              {h.title}
            </a>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export default function Dashboard() {
  const [chartDays, setChartDays] = useState(365);

  const dashboard   = useQuery({ queryKey: ["dashboard"],           queryFn: marketDashboardData,  refetchInterval: 60_000 });
  const chart       = useQuery({ queryKey: ["index-chart", chartDays], queryFn: () => marketIndexChart("VNINDEX", chartDays) });
  const news        = useQuery({ queryKey: ["economy-news"],        queryFn: () => economyNews(20) });
  const marketFlow        = useQuery({ queryKey: ["market-foreign-flow"],        queryFn: marketForeignFlowChart });
  const marketAnnualFlow  = useQuery({ queryKey: ["market-foreign-net-annual"],  queryFn: marketForeignNetAnnual });

  if (dashboard.isLoading) return (
    <div className="flex items-center gap-2 text-slate-500 py-20 justify-center">
      <RefreshCw size={16} className="animate-spin" /> Loading market data…
    </div>
  );

  const data = dashboard.data;
  if (!data) return null;
  const vn = data.indices.vnindex;

  return (
    <div className="space-y-5">
      <TickerBar data={data} />

      {/* Hero header */}
      <div>
        <p className="text-slate-500 text-xs font-medium uppercase tracking-wider mb-1">VN-Index</p>
        <div className="flex items-baseline gap-4 flex-wrap">
          <span className="text-5xl font-bold tabular-nums text-slate-900 dark:text-white">{fmt(vn.value)}</span>
          <ChangeTag pct={vn.change_pct} pts={vn.change} />
        </div>
        <p className="text-slate-400 text-xs mt-1">Vol: {fmtVol(vn.volume)}</p>
      </div>

      {/* Chart + indices */}
      <div className="grid xl:grid-cols-[1fr_300px] gap-5">
        <Card className="p-5">
          {chart.isFetching && !chart.data ? (
            <div className="h-[280px] flex items-center justify-center text-slate-400 text-sm">
              <RefreshCw size={14} className="animate-spin mr-2" /> Loading chart…
            </div>
          ) : chart.data && (
            <IndexChart prices={chart.data.prices} days={chartDays} onDaysChange={setChartDays} />
          )}
        </Card>

        <Card className="p-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3">Indices</p>
          <IndexRow label="VN-Index"  info={data.indices.vnindex} bold />
          <IndexRow label="HNX-Index" info={data.indices.hnx} />
          <IndexRow label="UPCOM"     info={data.indices.upcom} />
          {data.gainers[0] && (
            <>
              <p className="text-xs text-slate-400 uppercase tracking-wider mt-4 mb-2">Top Gainer</p>
              <div className="flex justify-between items-center">
                <span className="font-mono font-bold text-sm text-slate-800 dark:text-white">{data.gainers[0].ticker}</span>
                <ChangeTag pct={data.gainers[0].change_pct} />
              </div>
            </>
          )}
          {data.losers[0] && (
            <>
              <p className="text-xs text-slate-400 uppercase tracking-wider mt-3 mb-2">Top Loser</p>
              <div className="flex justify-between items-center">
                <span className="font-mono font-bold text-sm text-slate-800 dark:text-white">{data.losers[0].ticker}</span>
                <ChangeTag pct={data.losers[0].change_pct} />
              </div>
            </>
          )}
        </Card>
      </div>

      {/* Movers + news */}
      <div className="grid md:grid-cols-3 gap-4">
        <MoverTable title="Top Gainers" movers={data.gainers} color="green" loading={data.movers_age_s === null} />
        <MoverTable title="Top Losers"  movers={data.losers}  color="red"   loading={data.movers_age_s === null} />
        {news.data && <NewsStrip text={news.data.text} />}
      </div>

      <p className="text-slate-400 text-xs">
        Movers scanned from {data.universe_size > 0 ? data.universe_size : "…"} stocks
        {data.movers_age_s !== null
          ? ` · refreshed ${data.movers_age_s < 60 ? `${data.movers_age_s}s` : `${Math.round(data.movers_age_s / 60)}m`} ago`
          : " · loading initial scan…"}
      </p>

      {/* Market annual foreign flow — VN30 aggregated, 2019-present */}
      {marketAnnualFlow.isLoading && (
        <div className="flex items-center gap-2 text-slate-500 text-sm">
          <RefreshCw size={14} className="animate-spin" /> Loading market annual foreign flow…
        </div>
      )}
      {marketAnnualFlow.data && marketAnnualFlow.data.points.length > 0 && (
        <AnnualFlowChart
          ticker="VN30"
          points={marketAnnualFlow.data.points}
          subtitle="VCI · Aggregated across 30 large-caps"
        />
      )}

      {/* Market daily foreign flow */}
      {marketFlow.isLoading && (
        <div className="flex items-center gap-2 text-slate-500 text-sm">
          <RefreshCw size={14} className="animate-spin" /> Loading market foreign flow…
        </div>
      )}
      {marketFlow.data && marketFlow.data.points.length > 0 && (
        <MarketFlowChart data={marketFlow.data} />
      )}
    </div>
  );
}

const FLOW_PERIODS = [
  { label: "1M",  days: 21 },
  { label: "3M",  days: 63 },
  { label: "All", days: Infinity },
];

function MarketFlowChart({ data }: { data: ForeignFlowResponse }) {
  const [period, setPeriod] = useState(FLOW_PERIODS[2]);
  const fmt = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}B`;

  const visible = period.days === Infinity ? data.points : data.points.slice(-period.days);

  return (
    <Card className="p-5 space-y-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h3 className="font-semibold text-slate-800 dark:text-white">VN30 — Net Foreign Flow (B VND)</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Aggregated across 30 large-caps &nbsp;·&nbsp; Green = net buy &nbsp;·&nbsp; Red = net sell
            &nbsp;·&nbsp; <span className="text-slate-400">
              {data.points.length} sessions stored
              {data.points.length < 60 && " — history grows each visit"}
            </span>
          </p>
        </div>
        <div className="flex gap-1 shrink-0">
          {FLOW_PERIODS.map(p => (
            <button key={p.label} onClick={() => setPeriod(p)}
              className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${
                period.label === p.label
                  ? "bg-slate-800 dark:bg-slate-600 text-white"
                  : "text-slate-500 hover:text-slate-900 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-slate-700"
              }`}>
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={visible} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10 }}
            tickFormatter={(d: string) => d.slice(5)}
            interval="preserveStartEnd"
          />
          <YAxis
            tickFormatter={(v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(0)}B`}
            tick={{ fontSize: 11 }}
            width={56}
          />
          <ReferenceLine y={0} stroke="#94a3b8" />
          <Tooltip
            formatter={(value) => [fmt(Number(value ?? 0)), "Net value"]}
            labelFormatter={(label) => `Date: ${String(label)}`}
          />
          <Bar dataKey="net_val_b" radius={[2, 2, 0, 0]}>
            {visible.map((pt, i) => (
              <Cell key={i} fill={pt.net_val_b >= 0 ? "#10b981" : "#ef4444"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      {data.statements.length > 0 && (
        <ul className="space-y-1.5">
          {data.statements.map((s, i) => (
            <li key={i} className="flex items-start gap-2 text-xs text-slate-600 dark:text-slate-400">
              <span className={`mt-0.5 shrink-0 font-bold ${s.isPass ? "text-emerald-500" : "text-red-500"}`}>
                {s.isPass ? "▲" : "▼"}
              </span>
              {s.text}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
