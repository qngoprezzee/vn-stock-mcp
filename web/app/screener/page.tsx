"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { stockQualityScore } from "@/lib/api";
import { MarkdownBlock } from "@/components/MarkdownBlock";
import { Search, RefreshCw, AlertCircle, X } from "lucide-react";

type Result = {
  ticker: string;
  text?: string;
  error?: string;
  loading?: boolean;
};

export default function ScreenerPage() {
  const [input, setInput] = useState("FPT, VCB, HPG, MWG, VNM");
  const [results, setResults] = useState<Result[]>([]);

  const mutation = useMutation({
    mutationFn: async (tickers: string[]) => {
      setResults(tickers.map((t) => ({ ticker: t, loading: true })));
      const promises = tickers.map(async (ticker) => {
        try {
          const r = await stockQualityScore(ticker);
          return { ticker, text: r.text };
        } catch (e) {
          return { ticker, error: e instanceof Error ? e.message : "Unknown error" };
        }
      });
      const completed = await Promise.all(promises);
      setResults(completed);
      return completed;
    },
  });

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const tickers = input
      .split(/[,\s]+/)
      .map((t) => t.trim().toUpperCase())
      .filter(Boolean);
    if (tickers.length === 0) return;
    mutation.mutate(tickers);
  };

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">Quality Screen</h1>
        <p className="text-slate-600 dark:text-slate-400 mt-1">
          Score multiple tickers on ROIC, FCF/NI, debt, growth, and margins. Anything below 60 is
          rarely worth deeper research.
        </p>
      </header>

      <form onSubmit={onSubmit} className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm">
        <label htmlFor="tickers" className="block text-sm font-medium mb-2">
          Tickers to screen (comma or space separated)
        </label>
        <div className="flex gap-3">
          <input
            id="tickers"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="FPT, VCB, HPG..."
            className="flex-1 px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-md bg-white dark:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-900 dark:focus:ring-slate-100 uppercase tracking-wide"
          />
          <button
            type="submit"
            disabled={mutation.isPending}
            className="px-4 py-2 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded-md font-medium hover:opacity-90 disabled:opacity-50 flex items-center gap-2"
          >
            {mutation.isPending ? (
              <>
                <RefreshCw size={16} className="animate-spin" />
                Scoring...
              </>
            ) : (
              <>
                <Search size={16} />
                Screen
              </>
            )}
          </button>
        </div>
        <p className="text-xs text-slate-500 mt-2">
          First-time screening for a ticker takes 5-15s. Cached results return instantly.
        </p>
      </form>

      <div className="space-y-4">
        {results.map((r) => (
          <ResultCard key={r.ticker} result={r} onDismiss={() => setResults(results.filter((x) => x.ticker !== r.ticker))} />
        ))}
      </div>
    </div>
  );
}

function ResultCard({ result, onDismiss }: { result: Result; onDismiss: () => void }) {
  return (
    <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold">{result.ticker}</h3>
        <button
          onClick={onDismiss}
          className="text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
          aria-label={`Dismiss ${result.ticker}`}
        >
          <X size={16} />
        </button>
      </div>
      {result.loading && (
        <div className="flex items-center gap-2 text-slate-500 text-sm">
          <RefreshCw className="animate-spin" size={14} />
          Computing quality score...
        </div>
      )}
      {result.error && (
        <div className="flex items-start gap-2 text-red-700 dark:text-red-400 text-sm">
          <AlertCircle size={14} className="mt-0.5 shrink-0" />
          <span>{result.error}</span>
        </div>
      )}
      {result.text && <MarkdownBlock text={result.text} />}
    </section>
  );
}
