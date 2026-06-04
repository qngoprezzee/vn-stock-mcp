"use client";

import { useQuery } from "@tanstack/react-query";
import { decisionsRaw, reviewPerformance } from "@/lib/api";
import { MarkdownBlock } from "@/components/MarkdownBlock";
import { Activity, RefreshCw, TrendingUp, TrendingDown, Minus } from "lucide-react";

export default function PerformancePage() {
  const raw = useQuery({ queryKey: ["decisions-raw"], queryFn: decisionsRaw });
  const review = useQuery({
    queryKey: ["review", 365],
    queryFn: () => reviewPerformance(365),
  });

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">Performance Review</h1>
        <p className="text-slate-600 dark:text-slate-400 mt-1">
          Win rate, expectancy, and verdict from your decision journal. Review monthly or after
          every 10 closed trades.
        </p>
      </header>

      {raw.isLoading && <Spinner label="Loading decisions..." />}

      {raw.data && <MetricsRow metrics={raw.data.metrics} />}

      {raw.data && raw.data.closed_trades.length > 0 && (
        <ClosedTradesTable trades={raw.data.closed_trades} />
      )}

      {raw.data && Object.keys(raw.data.open_positions).length > 0 && (
        <OpenPositionsTable positions={raw.data.open_positions} />
      )}

      <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Activity size={18} />
          Full verdict & triage
        </h2>
        {review.isLoading && <Spinner label="Computing review..." />}
        {review.data && <MarkdownBlock text={review.data.text} />}
        {review.error && (
          <p className="text-sm text-red-600 dark:text-red-400">{review.error.message}</p>
        )}
      </section>
    </div>
  );
}

function MetricsRow({ metrics }: { metrics: Record<string, number | undefined> }) {
  const n = metrics.total_trades ?? 0;
  if (n === 0) {
    return (
      <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm">
        <p className="text-slate-600 dark:text-slate-400">
          No closed trades yet. Log decisions with the Decision Log tool to start building your
          track record.
        </p>
      </section>
    );
  }

  const exp = metrics.expectancy_pct ?? 0;
  const win = metrics.win_rate ?? 0;
  const expColor =
    exp > 3 ? "text-green-600 dark:text-green-400" :
    exp > 0 ? "text-yellow-600 dark:text-yellow-400" :
              "text-red-600 dark:text-red-400";

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
      <Metric label="Closed trades" value={String(n)} />
      <Metric label="Win rate" value={`${win.toFixed(0)}%`} sub={`${metrics.winners ?? 0}W / ${metrics.losers ?? 0}L`} />
      <Metric
        label="Expectancy"
        value={`${exp >= 0 ? "+" : ""}${exp.toFixed(2)}%`}
        valueClass={expColor}
        sub="per trade"
      />
      <Metric label="Avg winner" value={`+${(metrics.avg_winner_pct ?? 0).toFixed(1)}%`} />
      <Metric label="Avg loser" value={`${(metrics.avg_loser_pct ?? 0).toFixed(1)}%`} />
      <Metric
        label="Total P&L"
        value={`${(metrics.total_pnl ?? 0) / 1e6 > 0 ? "+" : ""}${((metrics.total_pnl ?? 0) / 1e6).toFixed(1)}M`}
        sub="VND"
      />
    </div>
  );
}

function Metric({
  label,
  value,
  sub,
  valueClass = "",
}: {
  label: string;
  value: string;
  sub?: string;
  valueClass?: string;
}) {
  return (
    <div className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-4 shadow-sm">
      <div className="text-xs text-slate-500 uppercase tracking-wide">{label}</div>
      <div className={`text-2xl font-semibold mt-1 ${valueClass}`}>{value}</div>
      {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

type Trade = {
  ticker: string;
  buy_date: string;
  sell_date: string;
  buy_price: number;
  sell_price: number;
  qty: number;
  pnl: number;
  pnl_pct: number;
  hold_days: number;
};

function ClosedTradesTable({ trades }: { trades: Trade[] }) {
  const sorted = [...trades].sort((a, b) => b.sell_date.localeCompare(a.sell_date)).slice(0, 20);
  return (
    <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm">
      <h2 className="text-lg font-semibold mb-4">Closed Trades (most recent 20)</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-slate-500 border-b border-slate-200 dark:border-slate-700">
              <th className="py-2 pr-4">Sell Date</th>
              <th className="py-2 pr-4">Ticker</th>
              <th className="py-2 pr-4 text-right">Hold (d)</th>
              <th className="py-2 pr-4 text-right">Entry</th>
              <th className="py-2 pr-4 text-right">Exit</th>
              <th className="py-2 pr-4 text-right">P&L %</th>
              <th className="py-2 pr-4 text-right">P&L (VND)</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((t, i) => (
              <tr
                key={`${t.ticker}-${t.sell_date}-${i}`}
                className="border-b border-slate-100 dark:border-slate-800"
              >
                <td className="py-2 pr-4 font-mono text-xs">{t.sell_date}</td>
                <td className="py-2 pr-4 font-medium flex items-center gap-1">
                  {t.pnl > 0 ? (
                    <TrendingUp size={14} className="text-green-600" />
                  ) : t.pnl < 0 ? (
                    <TrendingDown size={14} className="text-red-600" />
                  ) : (
                    <Minus size={14} className="text-slate-400" />
                  )}
                  {t.ticker}
                </td>
                <td className="py-2 pr-4 text-right">{t.hold_days}</td>
                <td className="py-2 pr-4 text-right">{t.buy_price.toLocaleString()}</td>
                <td className="py-2 pr-4 text-right">{t.sell_price.toLocaleString()}</td>
                <td
                  className={`py-2 pr-4 text-right font-medium ${
                    t.pnl_pct >= 0
                      ? "text-green-600 dark:text-green-400"
                      : "text-red-600 dark:text-red-400"
                  }`}
                >
                  {t.pnl_pct >= 0 ? "+" : ""}
                  {t.pnl_pct.toFixed(2)}%
                </td>
                <td
                  className={`py-2 pr-4 text-right font-mono text-xs ${
                    t.pnl >= 0
                      ? "text-green-600 dark:text-green-400"
                      : "text-red-600 dark:text-red-400"
                  }`}
                >
                  {t.pnl >= 0 ? "+" : ""}
                  {t.pnl.toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function OpenPositionsTable({
  positions,
}: {
  positions: Record<string, { qty: number; avg_cost: number; first_buy: string }>;
}) {
  const entries = Object.entries(positions).sort(([a], [b]) => a.localeCompare(b));
  return (
    <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm">
      <h2 className="text-lg font-semibold mb-4">Open Positions</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-slate-500 border-b border-slate-200 dark:border-slate-700">
              <th className="py-2 pr-4">Ticker</th>
              <th className="py-2 pr-4 text-right">Shares</th>
              <th className="py-2 pr-4 text-right">Avg Cost (VND)</th>
              <th className="py-2 pr-4">First Buy</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([ticker, p]) => (
              <tr key={ticker} className="border-b border-slate-100 dark:border-slate-800">
                <td className="py-2 pr-4 font-medium">{ticker}</td>
                <td className="py-2 pr-4 text-right">{p.qty.toLocaleString()}</td>
                <td className="py-2 pr-4 text-right">{p.avg_cost.toLocaleString()}</td>
                <td className="py-2 pr-4 font-mono text-xs">{p.first_buy}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 text-slate-500 text-sm">
      <RefreshCw className="animate-spin" size={14} />
      {label}
    </div>
  );
}
