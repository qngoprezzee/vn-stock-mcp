"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { stockNewsCorrelation } from "@/lib/api";
import { MarkdownBlock } from "@/components/MarkdownBlock";
import { GlossaryPanel } from "@/components/GlossaryPanel";
import { Activity, RefreshCw, AlertCircle, Newspaper } from "lucide-react";

const SAMPLE_TICKERS = ["FPT", "VCB", "HPG", "VNM", "MWG", "VIC", "HCM"];

export default function NewsImpactPage() {
  const [ticker, setTicker] = useState("FPT");
  const [lookbackDays, setLookbackDays] = useState("90");

  const mutation = useMutation({
    mutationFn: () =>
      stockNewsCorrelation({
        ticker: ticker.toUpperCase(),
        lookback_days: Number(lookbackDays),
      }),
  });

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
          <Newspaper size={28} />
          News ↔ Price Correlation
        </h1>
        <p className="text-slate-600 dark:text-slate-400 mt-1">
          Does this ticker&apos;s news flow lead, lag, or move with its price? Cross-correlation
          at lags −2 / −1 / 0 / +1 / +2 days, with keyword sentiment scoring (VN + EN).
        </p>
      </header>

      <section className="bg-amber-50/50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-900 rounded-md p-4 text-sm">
        <div className="flex items-start gap-2">
          <AlertCircle size={16} className="mt-0.5 shrink-0 text-amber-700 dark:text-amber-400" />
          <div className="text-amber-900 dark:text-amber-200">
            <strong>Honest about limitations:</strong> Sentiment uses keyword lexicons
            (~70% accuracy, can&apos;t catch sarcasm). Statistical power is low at ~60-90
            trading days. Tickers with fewer than 15 articles return noise.
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
          <label htmlFor="lookback" className="block text-sm font-medium mb-1">Lookback (days)</label>
          <input
            id="lookback"
            type="number"
            min="30" max="365" step="15"
            value={lookbackDays}
            onChange={(e) => setLookbackDays(e.target.value)}
            className="w-full px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-md bg-white dark:bg-slate-800"
          />
          <p className="text-xs text-slate-500 mt-1">90+ recommended for signal</p>
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
                Computing correlations...
              </>
            ) : (
              <>
                <Activity size={16} />
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
        <>
          <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm">
            <MarkdownBlock text={mutation.data.text} />
          </section>

          <GlossaryPanel
            title="How to read these correlations"
            conceptKeys={["sensitivity_grid", "margin_of_safety"]}
          />
        </>
      )}
    </div>
  );
}
