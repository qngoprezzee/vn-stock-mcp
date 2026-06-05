"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import { Search, RefreshCw, TrendingUp, TrendingDown, Minus } from "lucide-react";
import { stockChartData, stockOverviewData, stockIncomeTrend, stockExecutiveSummary } from "@/lib/api";
import { TradingChart } from "@/components/TradingChart";
import { ExecutiveSummarySection } from "@/components/ExecutiveSummary";
import type { OverviewData, IncomeTrend, Indicators } from "@/lib/api";

const VND = (n: number) => n.toLocaleString("vi-VN") + " ₫";
const B   = (n: number | null) => n == null ? "—" : n.toLocaleString("vi-VN", { maximumFractionDigits: 1 }) + " B";

const DEFAULT_INDICATORS: Indicators = {
  ma20: true, ma50: true, ma200: false,
  bb: false, rsi: true, macd: false, volume: true,
};

export default function StockPage() {
  const [input, setInput]         = useState("FPT");
  const [ticker, setTicker]       = useState("");
  const [indicators, setIndicators] = useState<Indicators>(DEFAULT_INDICATORS);

  const overview = useQuery({
    queryKey: ["overview-data", ticker],
    queryFn: () => stockOverviewData(ticker),
    enabled: !!ticker,
  });

  const chart = useQuery({
    queryKey: ["chart-data", ticker],
    queryFn: () => stockChartData(ticker, 365),
    enabled: !!ticker,
  });

  const income = useQuery({
    queryKey: ["income-trend", ticker],
    queryFn: () => stockIncomeTrend(ticker),
    enabled: !!ticker,
  });

  const execSummary = useQuery({
    queryKey: ["executive-summary", ticker],
    queryFn: () => stockExecutiveSummary(ticker),
    enabled: !!ticker,
  });

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = input.trim().toUpperCase();
    if (t) setTicker(t);
  };

  const toggle = (key: keyof Indicators) =>
    setIndicators(prev => ({ ...prev, [key]: !prev[key] }));

  const loading = overview.isFetching || chart.isFetching;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">Stock Charts</h1>
        <p className="text-slate-500 mt-1 text-sm">
          Candlestick chart with MA, Bollinger Bands, RSI and MACD — powered by vnstock.
        </p>
      </header>

      {/* Ticker search */}
      <form onSubmit={onSubmit} className="flex gap-3 flex-wrap">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value.toUpperCase())}
          placeholder="FPT"
          className="w-28 px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-md bg-white dark:bg-slate-800 font-mono uppercase tracking-widest focus:outline-none focus:ring-2 focus:ring-slate-900 dark:focus:ring-slate-100"
        />
        <button
          type="submit"
          disabled={loading}
          className="px-4 py-2 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded-md font-medium hover:opacity-90 disabled:opacity-50 flex items-center gap-2"
        >
          {loading ? <RefreshCw size={16} className="animate-spin" /> : <Search size={16} />}
          Load
        </button>
      </form>

      {!ticker && (
        <p className="text-slate-400 text-sm">Enter a ticker symbol and press Load.</p>
      )}

      {/* Metrics */}
      {overview.data && <MetricsRow data={overview.data} />}

      {/* Indicator toggles */}
      {ticker && (
        <div className="flex flex-wrap gap-2">
          {(Object.keys(DEFAULT_INDICATORS) as (keyof Indicators)[]).map(key => (
            <button
              key={key}
              onClick={() => toggle(key)}
              className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
                indicators[key]
                  ? "bg-slate-800 border-slate-600 text-white"
                  : "bg-white dark:bg-slate-900 border-slate-300 dark:border-slate-700 text-slate-500"
              }`}
            >
              {key.toUpperCase()}
            </button>
          ))}
        </div>
      )}

      {/* TradingView-like candlestick chart */}
      {chart.data && chart.data.prices.length > 0 && (
        <TradingChart
          prices={chart.data.prices}
          ticker={ticker}
          indicators={indicators}
        />
      )}

      {chart.isLoading && (
        <div className="flex items-center gap-2 text-slate-500 text-sm">
          <RefreshCw size={14} className="animate-spin" /> Loading price data…
        </div>
      )}

      {/* Annual financials */}
      {/* Executive Summary from Simplize */}
      {execSummary.data && (
        <section className="bg-slate-900/40 border border-slate-800 rounded-xl p-5">
          <ExecutiveSummarySection data={execSummary.data} />
        </section>
      )}
      {execSummary.isLoading && (
        <div className="flex items-center gap-2 text-slate-500 text-sm">
          <RefreshCw size={14} className="animate-spin" /> Loading executive summary…
        </div>
      )}

      {income.data && income.data.years.length > 0 && (
        <IncomeChart data={income.data} />
      )}

      {(overview.error || chart.error) && (
        <p className="text-red-600 text-sm">
          {String((overview.error || chart.error) as Error)}
        </p>
      )}
    </div>
  );
}

function MetricsRow({ data }: { data: OverviewData }) {
  const up = data.change_pct > 0;
  const dn = data.change_pct < 0;
  return (
    <div className="space-y-3">
      <div className="flex items-baseline gap-3 flex-wrap">
        <h2 className="text-xl font-bold">{data.ticker}</h2>
        <span className="text-slate-500 text-sm">{data.name}</span>
        <span className="text-xs px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300">
          {data.sector}
        </span>
      </div>

      <div className="flex items-baseline gap-4 flex-wrap">
        <span className="text-4xl font-bold tabular-nums">{VND(data.price)}</span>
        <span className={`flex items-center gap-1 text-lg font-semibold ${up ? "text-emerald-600" : dn ? "text-red-600" : "text-slate-500"}`}>
          {up ? <TrendingUp size={18} /> : dn ? <TrendingDown size={18} /> : <Minus size={18} />}
          {data.change_pct > 0 ? "+" : ""}{data.change_pct.toFixed(2)}%
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {[
          { label: "Market Cap",   value: `${data.market_cap_t.toFixed(2)} T` },
          { label: "52W High",     value: VND(data.high_52w) },
          { label: "52W Low",      value: VND(data.low_52w) },
          { label: "Target Price", value: data.target_price ? VND(data.target_price) : "—" },
          { label: "Foreign Own",  value: `${data.foreign_pct}%` },
          { label: "Rating",       value: data.rating || "—" },
        ].map(({ label, value }) => (
          <div key={label} className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg p-3">
            <p className="text-xs text-slate-500 mb-1">{label}</p>
            <p className="font-semibold text-sm tabular-nums">{value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function IncomeChart({ data }: { data: IncomeTrend }) {
  const chartData = data.years.map((year, i) => ({
    year,
    Revenue:        data.revenue[i],
    "Net Income":   data.net_income[i],
    "Gross Profit": data.gross_profit[i],
  }));

  return (
    <section className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg p-5 shadow-sm">
      <h3 className="font-semibold mb-4">{data.ticker} — Annual Financials (B VND)</h3>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="year" tick={{ fontSize: 12 }} />
          <YAxis
            tickFormatter={(v) => v >= 1000 ? `${(v / 1000).toFixed(0)}T` : `${v}B`}
            tick={{ fontSize: 11 }}
            width={44}
          />
          <Tooltip formatter={(value, name) => [`${B(Number(value))}`, String(name)]} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar dataKey="Revenue"       fill="#3b82f6" radius={[2, 2, 0, 0]} />
          <Bar dataKey="Gross Profit"  fill="#10b981" radius={[2, 2, 0, 0]} />
          <Bar dataKey="Net Income"    fill="#f59e0b" radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </section>
  );
}
