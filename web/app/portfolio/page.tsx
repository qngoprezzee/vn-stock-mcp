"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  portfolioRaw,
  portfolioManage,
  portfolioOverview,
  portfolioRisk,
  portfolioRebalance,
  portfolioReturns,
  portfolioSnapshots,
} from "@/lib/api";
import { MarkdownBlock } from "@/components/MarkdownBlock";
import { Briefcase, Wallet, Plus, X, RefreshCw, BarChart3, Shield, Scale, AlertCircle, LineChart } from "lucide-react";

const VND = (n: number) => n.toLocaleString("vi-VN");

const emptyForm = {
  ticker: "",
  shares: "",
  avg_cost: "",
  target_weight: "",
  notes: "",
};

export default function PortfolioPage() {
  const qc = useQueryClient();
  const [form, setForm] = useState(emptyForm);
  const [cashInput, setCashInput] = useState("");
  const [rebalanceThreshold, setRebalanceThreshold] = useState("3");

  const portfolio = useQuery({
    queryKey: ["portfolio-raw"],
    queryFn: portfolioRaw,
  });

  const invalidatePortfolio = () => {
    qc.invalidateQueries({ queryKey: ["portfolio-raw"] });
  };

  const addPosition = useMutation({
    mutationFn: () =>
      portfolioManage({
        action: "add",
        ticker: form.ticker.toUpperCase(),
        shares: Number(form.shares),
        avg_cost: Number(form.avg_cost),
        target_weight: form.target_weight ? Number(form.target_weight) : undefined,
        notes: form.notes || undefined,
      }),
    onSuccess: () => {
      setForm(emptyForm);
      invalidatePortfolio();
    },
  });

  const removePosition = useMutation({
    mutationFn: (ticker: string) =>
      portfolioManage({ action: "remove", ticker }),
    onSuccess: invalidatePortfolio,
  });

  const setCash = useMutation({
    mutationFn: () =>
      portfolioManage({ action: "set_cash", cash_vnd: Number(cashInput) }),
    onSuccess: () => {
      setCashInput("");
      invalidatePortfolio();
    },
  });

  const overview = useMutation({ mutationFn: portfolioOverview });
  const risk = useMutation({ mutationFn: portfolioRisk });
  const rebalance = useMutation({
    mutationFn: () => portfolioRebalance({ threshold_pct: Number(rebalanceThreshold) || 3 }),
  });
  const returns = useMutation({ mutationFn: () => portfolioReturns({}) });

  const snapshots = useQuery({
    queryKey: ["portfolio-snapshots"],
    queryFn: portfolioSnapshots,
  });
  const snapshotCount = snapshots.data?.snapshots.length ?? 0;

  const holdings = portfolio.data?.holdings ?? [];
  const cash = portfolio.data?.cash_vnd ?? 0;
  const peakValue = portfolio.data?.peak_value ?? 0;
  const peakDate = portfolio.data?.peak_date ?? "";

  const canAdd =
    form.ticker.trim().length > 0 && Number(form.shares) > 0 && Number(form.avg_cost) > 0;

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
          <Briefcase size={28} />
          Portfolio &amp; Risk Manager
        </h1>
        <p className="text-slate-600 dark:text-slate-400 mt-1">
          Persistent holdings in <code>.portfolio.json</code>. Manage positions, then run
          overview / risk / rebalance analyses on the current state.
        </p>
      </header>

      {/* ── Holdings ─────────────────────────────────────────────────────── */}
      <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Holdings ({holdings.length})</h2>
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <Wallet size={14} />
            Cash: <span className="font-mono">{VND(cash)} VND</span>
            {peakValue > 0 && (
              <>
                <span className="mx-1">·</span>
                Peak: <span className="font-mono">{VND(Math.round(peakValue))}</span>
                {peakDate && <span className="text-slate-400 ml-1">({peakDate})</span>}
              </>
            )}
          </div>
        </div>

        {portfolio.isLoading ? (
          <div className="text-sm text-slate-500 flex items-center gap-2">
            <RefreshCw size={14} className="animate-spin" /> Loading...
          </div>
        ) : holdings.length === 0 ? (
          <div className="text-sm text-slate-500 italic">
            No positions yet. Add your first below.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b border-slate-200 dark:border-slate-800 text-slate-500">
                  <th className="py-2 px-2 font-medium">Ticker</th>
                  <th className="py-2 px-2 font-medium text-right">Shares</th>
                  <th className="py-2 px-2 font-medium text-right">Avg Cost</th>
                  <th className="py-2 px-2 font-medium text-right">Target %</th>
                  <th className="py-2 px-2 font-medium">Opened</th>
                  <th className="py-2 px-2 font-medium">Notes</th>
                  <th className="py-2 px-2"></th>
                </tr>
              </thead>
              <tbody>
                {holdings.map((h) => (
                  <tr key={h.ticker} className="border-b border-slate-100 dark:border-slate-800/60">
                    <td className="py-2 px-2 font-mono font-medium">{h.ticker}</td>
                    <td className="py-2 px-2 text-right font-mono">{VND(h.shares)}</td>
                    <td className="py-2 px-2 text-right font-mono">{VND(h.avg_cost)}</td>
                    <td className="py-2 px-2 text-right font-mono">
                      {h.target_weight != null ? `${h.target_weight.toFixed(1)}%` : "—"}
                    </td>
                    <td className="py-2 px-2 text-slate-500 text-xs">{h.opened_at ?? "—"}</td>
                    <td className="py-2 px-2 text-slate-500 text-xs truncate max-w-[200px]">{h.notes ?? ""}</td>
                    <td className="py-2 px-2 text-right">
                      <button
                        onClick={() => removePosition.mutate(h.ticker)}
                        disabled={removePosition.isPending}
                        className="p-1 rounded hover:bg-red-50 dark:hover:bg-red-950/40 text-red-600 disabled:opacity-40"
                        title="Remove position"
                      >
                        <X size={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Add form */}
        <div className="pt-4 border-t border-slate-200 dark:border-slate-800">
          <h3 className="text-sm font-semibold mb-3">Add / Replace Position</h3>
          <form
            className="grid grid-cols-2 md:grid-cols-6 gap-3 items-end"
            onSubmit={(e) => {
              e.preventDefault();
              if (canAdd) addPosition.mutate();
            }}
          >
            <div>
              <label className="block text-xs text-slate-500 mb-1">Ticker</label>
              <input
                value={form.ticker}
                onChange={(e) => setForm({ ...form, ticker: e.target.value.toUpperCase() })}
                placeholder="FPT"
                className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 uppercase font-mono text-sm"
                required
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Shares</label>
              <input
                type="number" min="1" step="1"
                value={form.shares}
                onChange={(e) => setForm({ ...form, shares: e.target.value })}
                className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
                required
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Avg Cost (VND)</label>
              <input
                type="number" min="1" step="100"
                value={form.avg_cost}
                onChange={(e) => setForm({ ...form, avg_cost: e.target.value })}
                className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
                required
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Target %</label>
              <input
                type="number" min="0" max="100" step="0.5"
                value={form.target_weight}
                onChange={(e) => setForm({ ...form, target_weight: e.target.value })}
                placeholder="optional"
                className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
              />
            </div>
            <div className="col-span-2 md:col-span-1">
              <label className="block text-xs text-slate-500 mb-1">Notes</label>
              <input
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
                placeholder="optional"
                className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
              />
            </div>
            <div>
              <button
                type="submit"
                disabled={!canAdd || addPosition.isPending}
                className="w-full px-3 py-1.5 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded font-medium text-sm hover:opacity-90 disabled:opacity-50 flex items-center gap-1.5 justify-center"
              >
                <Plus size={14} /> Add
              </button>
            </div>
          </form>
          {addPosition.error && (
            <p className="text-xs text-red-600 mt-2">{addPosition.error.message}</p>
          )}
        </div>

        {/* Cash */}
        <div className="pt-4 border-t border-slate-200 dark:border-slate-800">
          <h3 className="text-sm font-semibold mb-3">Set Cash Balance</h3>
          <div className="flex gap-3 items-end">
            <div className="flex-1 max-w-xs">
              <input
                type="number" min="0" step="1000000"
                value={cashInput}
                onChange={(e) => setCashInput(e.target.value)}
                placeholder="VND (e.g. 50000000)"
                className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
              />
            </div>
            <button
              onClick={() => setCash.mutate()}
              disabled={!cashInput || setCash.isPending}
              className="px-3 py-1.5 bg-slate-700 dark:bg-slate-300 text-white dark:text-slate-900 rounded font-medium text-sm hover:opacity-90 disabled:opacity-50 flex items-center gap-1.5"
            >
              <Wallet size={14} /> Set Cash
            </button>
          </div>
        </div>
      </section>

      {/* ── Analyses ─────────────────────────────────────────────────────── */}
      <section className="grid md:grid-cols-4 gap-4">
        <AnalysisCard
          icon={<BarChart3 size={18} />}
          title="Overview"
          hint="Total value, P&L, sector allocation, drawdown."
          onRun={() => {
            overview.mutate(undefined, {
              onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio-snapshots"] }),
            });
          }}
          pending={overview.isPending}
          disabled={holdings.length === 0 && cash === 0}
        />
        <AnalysisCard
          icon={<Shield size={18} />}
          title="Risk"
          hint="Concentration, beta, correlations, drawdown, verdict."
          onRun={() => risk.mutate()}
          pending={risk.isPending}
          disabled={holdings.length === 0}
        />
        <AnalysisCard
          icon={<Scale size={18} />}
          title="Rebalance"
          hint="Current vs target weights, trades to execute."
          onRun={() => rebalance.mutate()}
          pending={rebalance.isPending}
          disabled={holdings.length === 0}
          extra={
            <div className="mt-2">
              <label className="block text-xs text-slate-500 mb-1">Threshold %</label>
              <input
                type="number" min="0.5" max="20" step="0.5"
                value={rebalanceThreshold}
                onChange={(e) => setRebalanceThreshold(e.target.value)}
                className="w-full px-2 py-1 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-xs"
              />
            </div>
          }
        />
        <AnalysisCard
          icon={<LineChart size={18} />}
          title="Returns"
          hint={`TWR, CAGR, YTD, Sharpe, vs VN-Index. ${snapshotCount} snapshots stored.`}
          onRun={() => returns.mutate()}
          pending={returns.isPending}
          disabled={snapshotCount < 2}
        />
      </section>

      {overview.data && (
        <ResultPanel>
          <MarkdownBlock text={overview.data.text} />
        </ResultPanel>
      )}
      {overview.error && <ErrorBanner message={overview.error.message} />}

      {risk.data && (
        <ResultPanel>
          <MarkdownBlock text={risk.data.text} />
        </ResultPanel>
      )}
      {risk.error && <ErrorBanner message={risk.error.message} />}

      {rebalance.data && (
        <ResultPanel>
          <MarkdownBlock text={rebalance.data.text} />
        </ResultPanel>
      )}
      {rebalance.error && <ErrorBanner message={rebalance.error.message} />}

      {returns.data && (
        <ResultPanel>
          <MarkdownBlock text={returns.data.text} />
        </ResultPanel>
      )}
      {returns.error && <ErrorBanner message={returns.error.message} />}
    </div>
  );
}

function AnalysisCard({
  icon, title, hint, onRun, pending, disabled, extra,
}: {
  icon: React.ReactNode;
  title: string;
  hint: string;
  onRun: () => void;
  pending: boolean;
  disabled?: boolean;
  extra?: React.ReactNode;
}) {
  return (
    <div className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-4 shadow-sm">
      <div className="flex items-center gap-2 mb-1">
        {icon}
        <h3 className="font-semibold">{title}</h3>
      </div>
      <p className="text-xs text-slate-500 mb-3">{hint}</p>
      <button
        onClick={onRun}
        disabled={pending || disabled}
        className="w-full px-3 py-1.5 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded font-medium text-sm hover:opacity-90 disabled:opacity-40 flex items-center justify-center gap-1.5"
      >
        {pending ? <RefreshCw size={14} className="animate-spin" /> : icon}
        {pending ? "Running..." : "Run"}
      </button>
      {extra}
    </div>
  );
}

function ResultPanel({ children }: { children: React.ReactNode }) {
  return (
    <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm">
      {children}
    </section>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="rounded-md bg-red-50 dark:bg-red-950/50 border border-red-300 dark:border-red-800 p-4 flex items-start gap-2 text-red-700 dark:text-red-400 text-sm">
      <AlertCircle size={16} className="mt-0.5 shrink-0" />
      <span>{message}</span>
    </div>
  );
}
