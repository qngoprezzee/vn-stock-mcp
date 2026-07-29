"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { stockMoneyFlowPriceAction } from "@/lib/api";
import { MarkdownBlock } from "@/components/MarkdownBlock";
import { Waves, RefreshCw, AlertCircle } from "lucide-react";

const SAMPLE_TICKERS = ["FPT", "VCB", "HPG", "VNM", "MWG", "VIC", "HCM", "MBB"];

export default function MoneyFlowPage() {
  const [ticker, setTicker] = useState("FPT");
  const [days, setDays] = useState("180");

  const mutation = useMutation({
    mutationFn: () =>
      stockMoneyFlowPriceAction({
        ticker: ticker.toUpperCase(),
        days: Number(days),
      }),
  });

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
          <Waves size={28} />
          Money Flow &amp; Price Action
        </h1>
        <p className="text-slate-600 dark:text-slate-400 mt-1">
          Dòng tiền &amp; hành động giá — MFI · OBV · CMF · A/D, up-vs-down volume,
          candlestick patterns, HH/HL structure, gaps, breakouts &amp; divergence.
          Surfaces whether smart money is accumulating or distributing.
        </p>
      </header>

      <section className="bg-amber-50/50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-900 rounded-md p-4 text-sm">
        <div className="flex items-start gap-2">
          <AlertCircle size={16} className="mt-0.5 shrink-0 text-amber-700 dark:text-amber-400" />
          <div className="text-amber-900 dark:text-amber-200">
            <strong>Complementary to Technical:</strong> This tool focuses on
            volume × price synthesis (money flow) and pure candle reading (price action).
            Use alongside <code>/technical</code> (MA/RSI/MACD) and <code>/stock</code> (foreign flow)
            for a complete tape read.
          </div>
        </div>
      </section>

      <form
        className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm grid md:grid-cols-3 gap-4 items-end"
        onSubmit={(e) => {
          e.preventDefault();
          mutation.mutate();
        }}
      >
        <div className="md:col-span-2">
          <label htmlFor="ticker" className="block text-sm font-medium mb-1">Ticker</label>
          <input
            id="ticker"
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            className="w-full px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-md bg-white dark:bg-slate-800 uppercase tracking-wide"
            required
          />
          <div className="mt-2 flex flex-wrap gap-1.5">
            {SAMPLE_TICKERS.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTicker(t)}
                className="text-xs px-2 py-1 rounded border border-slate-300 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800"
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label htmlFor="days" className="block text-sm font-medium mb-1">History (days)</label>
          <input
            id="days"
            type="number"
            min="60" max="720" step="30"
            value={days}
            onChange={(e) => setDays(e.target.value)}
            className="w-full px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-md bg-white dark:bg-slate-800"
          />
          <p className="text-xs text-slate-500 mt-1">180 default; ≥120 recommended</p>
        </div>

        <div className="md:col-span-3">
          <button
            type="submit"
            disabled={mutation.isPending}
            className="px-6 py-2 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded-md font-medium hover:opacity-90 disabled:opacity-50 flex items-center gap-2"
          >
            {mutation.isPending ? (
              <>
                <RefreshCw size={16} className="animate-spin" />
                Reading the tape...
              </>
            ) : (
              <>
                <Waves size={16} />
                Analyze
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
    </div>
  );
}
