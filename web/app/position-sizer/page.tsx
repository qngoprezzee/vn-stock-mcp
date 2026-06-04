"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { positionSizing, type Conviction } from "@/lib/api";
import { MarkdownBlock } from "@/components/MarkdownBlock";
import { Calculator, RefreshCw, AlertCircle } from "lucide-react";

export default function PositionSizerPage() {
  const [ticker, setTicker] = useState("FPT");
  const [portfolioM, setPortfolioM] = useState("500"); // millions VND
  const [riskPct, setRiskPct] = useState("2.0");
  const [conviction, setConviction] = useState<Conviction>("medium");
  const [atrMult, setAtrMult] = useState("2.0");

  const mutation = useMutation({
    mutationFn: () =>
      positionSizing({
        ticker: ticker.toUpperCase(),
        portfolio_value: Number(portfolioM) * 1_000_000,
        risk_per_trade_pct: Number(riskPct),
        conviction,
        atr_multiplier: Number(atrMult),
      }),
  });

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">Position Sizer</h1>
        <p className="text-slate-600 dark:text-slate-400 mt-1">
          ATR-based stop-loss + fixed-fractional risk. Returns max shares, stop price, and R/R at
          1:1, 2:1, 3:1 targets. Size before you buy, every time.
        </p>
      </header>

      <form
        className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm grid md:grid-cols-2 gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          mutation.mutate();
        }}
      >
        <Field label="Ticker">
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            className="input"
            required
          />
        </Field>

        <Field label="Portfolio value (M VND)" hint="e.g. 500 = 500M VND">
          <input
            value={portfolioM}
            onChange={(e) => setPortfolioM(e.target.value)}
            type="number"
            min="1"
            step="any"
            className="input"
            required
          />
        </Field>

        <Field label="Risk per trade (%)" hint="2% is a common max for retail discipline">
          <input
            value={riskPct}
            onChange={(e) => setRiskPct(e.target.value)}
            type="number"
            min="0.1"
            max="10"
            step="0.1"
            className="input"
            required
          />
        </Field>

        <Field label="Conviction" hint="Scales risk: low 0.5x, medium 1x, high 1.5x">
          <select
            value={conviction}
            onChange={(e) => setConviction(e.target.value as Conviction)}
            className="input"
          >
            <option value="low">Low (0.5x)</option>
            <option value="medium">Medium (1.0x)</option>
            <option value="high">High (1.5x)</option>
          </select>
        </Field>

        <Field label="ATR multiplier" hint="2.0 = stop 2× ATR below entry">
          <input
            value={atrMult}
            onChange={(e) => setAtrMult(e.target.value)}
            type="number"
            min="0.5"
            max="5"
            step="0.1"
            className="input"
          />
        </Field>

        <div className="md:col-span-2 pt-2">
          <button
            type="submit"
            disabled={mutation.isPending}
            className="w-full md:w-auto px-6 py-2 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded-md font-medium hover:opacity-90 disabled:opacity-50 flex items-center gap-2 justify-center"
          >
            {mutation.isPending ? (
              <>
                <RefreshCw size={16} className="animate-spin" />
                Calculating...
              </>
            ) : (
              <>
                <Calculator size={16} />
                Size position
              </>
            )}
          </button>
        </div>
      </form>

      {mutation.error && (
        <div className="rounded-md bg-red-50 dark:bg-red-950/50 border border-red-300 dark:border-red-800 p-4 flex items-start gap-2 text-red-700 dark:text-red-400 text-sm">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>{mutation.error.message}</span>
        </div>
      )}

      {mutation.data && (
        <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm">
          <MarkdownBlock text={mutation.data.text} />
        </section>
      )}

      <style>{`
        .input {
          width: 100%;
          padding: 0.5rem 0.75rem;
          border: 1px solid rgb(203 213 225);
          border-radius: 0.375rem;
          background: white;
          color: rgb(15 23 42);
        }
        @media (prefers-color-scheme: dark) {
          .input { background: rgb(30 41 59); color: rgb(241 245 249); border-color: rgb(51 65 85); }
        }
        .input:focus { outline: 2px solid rgb(15 23 42); outline-offset: -1px; }
      `}</style>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="block text-sm font-medium mb-1">{label}</span>
      {children}
      {hint && <span className="block text-xs text-slate-500 mt-1">{hint}</span>}
    </label>
  );
}
