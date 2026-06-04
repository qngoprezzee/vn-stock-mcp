"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { stockDCF } from "@/lib/api";
import { MarkdownBlock } from "@/components/MarkdownBlock";
import { GlossaryPanel } from "@/components/GlossaryPanel";
import { Calculator, RefreshCw, AlertCircle, Target } from "lucide-react";

export default function ValuationPage() {
  const [ticker, setTicker] = useState("FPT");
  const [discountRate, setDiscountRate] = useState("12");
  const [terminalGrowth, setTerminalGrowth] = useState("5");
  const [bullGrowth, setBullGrowth] = useState("20");
  const [baseGrowth, setBaseGrowth] = useState("12");
  const [bearGrowth, setBearGrowth] = useState("5");
  const [projectionYears, setProjectionYears] = useState("5");
  const [peersInput, setPeersInput] = useState("");

  const mutation = useMutation({
    mutationFn: () =>
      stockDCF({
        ticker: ticker.toUpperCase(),
        discount_rate:    Number(discountRate),
        terminal_growth:  Number(terminalGrowth),
        bull_growth:      Number(bullGrowth),
        base_growth:      Number(baseGrowth),
        bear_growth:      Number(bearGrowth),
        projection_years: Number(projectionYears),
        ...(peersInput.trim()
          ? { peers: peersInput.split(",").map((p) => p.trim().toUpperCase()).filter(Boolean) }
          : {}),
      }),
  });

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
          <Target size={28} />
          Triangulated Valuation
        </h1>
        <p className="text-slate-600 dark:text-slate-400 mt-1">
          DCF + peer-relative + sensitivity grid, blended by sector. Banks lean
          relative-heavy; staples lean DCF-heavy. Surfaces brittleness honestly.
        </p>
      </header>

      <form
        className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm grid md:grid-cols-3 gap-4"
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

        <Field label="WACC / discount rate (%)" hint="VN hurdle ~12%">
          <input
            type="number" min="1" max="50" step="0.5"
            value={discountRate}
            onChange={(e) => setDiscountRate(e.target.value)}
            className="input"
          />
        </Field>

        <Field label="Terminal growth (%)" hint="VN long-term GDP ~5%">
          <input
            type="number" min="0" max="15" step="0.5"
            value={terminalGrowth}
            onChange={(e) => setTerminalGrowth(e.target.value)}
            className="input"
          />
        </Field>

        <Field label="Bull FCF growth (%)">
          <input
            type="number" min="0" max="60" step="1"
            value={bullGrowth}
            onChange={(e) => setBullGrowth(e.target.value)}
            className="input"
          />
        </Field>

        <Field label="Base FCF growth (%)">
          <input
            type="number" min="-10" max="50" step="1"
            value={baseGrowth}
            onChange={(e) => setBaseGrowth(e.target.value)}
            className="input"
          />
        </Field>

        <Field label="Bear FCF growth (%)">
          <input
            type="number" min="-20" max="30" step="1"
            value={bearGrowth}
            onChange={(e) => setBearGrowth(e.target.value)}
            className="input"
          />
        </Field>

        <Field label="Projection years">
          <input
            type="number" min="3" max="10" step="1"
            value={projectionYears}
            onChange={(e) => setProjectionYears(e.target.value)}
            className="input"
          />
        </Field>

        <Field label="Custom peers (optional)" hint="Comma-separated, overrides default peer set" full>
          <input
            value={peersInput}
            onChange={(e) => setPeersInput(e.target.value)}
            placeholder="e.g. CMG, VGI, ITD"
            className="input"
          />
        </Field>

        <div className="md:col-span-3 pt-2">
          <button
            type="submit"
            disabled={mutation.isPending}
            className="px-6 py-2 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded-md font-medium hover:opacity-90 disabled:opacity-50 flex items-center gap-2"
          >
            {mutation.isPending ? (
              <>
                <RefreshCw size={16} className="animate-spin" />
                Triangulating...
              </>
            ) : (
              <>
                <Calculator size={16} />
                Run valuation
              </>
            )}
          </button>
          <p className="text-xs text-slate-500 mt-2">
            First run takes 30-60s (fetches financials + peer multiples in parallel). Cached calls return instantly.
          </p>
        </div>

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
            title="What's behind these numbers?"
            conceptKeys={[
              "triangulation",
              "dcf",
              "fcf",
              "intrinsic_value",
              "wacc",
              "terminal_growth",
              "peer_relative",
              "pe_ratio",
              "pb_ratio",
              "ev_ebitda",
              "sensitivity_grid",
              "margin_of_safety",
            ]}
          />
        </>
      )}
    </div>
  );
}

function Field({
  label,
  hint,
  children,
  full,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
  full?: boolean;
}) {
  return (
    <label className={`block ${full ? "md:col-span-3" : ""}`}>
      <span className="block text-sm font-medium mb-1">{label}</span>
      {children}
      {hint && <span className="block text-xs text-slate-500 mt-1">{hint}</span>}
    </label>
  );
}
