"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { saveThesis, knowledgeThesisContext } from "@/lib/api";
import { MarkdownBlock } from "@/components/MarkdownBlock";
import { FileText, RefreshCw, AlertCircle, BookOpen } from "lucide-react";

export default function ThesisPage() {
  const [form, setForm] = useState({
    ticker: "FPT",
    thesis: "",
    buy_price: "",
    target_price: "",
    stop_price: "",
    conviction: "Medium",
    falsification_criteria: "",
    catalysts: "",
    strongest_bias: "",
    premortem_reason: "",
  });

  const context = useMutation({
    mutationFn: (ticker: string) =>
      knowledgeThesisContext({
        ticker: ticker.toUpperCase(),
        lookback_days: 30,
        include_sector_principles: true,
      }),
  });

  const loadContext = () => {
    if (form.ticker.trim()) context.mutate(form.ticker.trim());
  };

  const mutation = useMutation({
    mutationFn: () =>
      saveThesis({
        ticker: form.ticker.toUpperCase(),
        thesis: form.thesis,
        buy_price: Number(form.buy_price),
        target_price: Number(form.target_price),
        stop_price: Number(form.stop_price),
        conviction: form.conviction,
        falsification_criteria: form.falsification_criteria,
        catalysts: form.catalysts,
        strongest_bias: form.strongest_bias,
        premortem_reason: form.premortem_reason,
      }),
  });

  const update = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setForm({ ...form, [key]: e.target.value });

  const canSubmit =
    form.ticker && form.thesis && form.buy_price && form.target_price && form.stop_price && form.falsification_criteria;

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">New Investment Thesis</h1>
        <p className="text-slate-600 dark:text-slate-400 mt-1">
          Write before you buy. A thesis written after entry is rationalization. The pre-mortem
          fields are the highest-leverage discipline here.
        </p>
      </header>

      <section className="bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-900 rounded-lg p-6">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <BookOpen size={18} className="text-blue-700 dark:text-blue-300" />
            <h2 className="text-base font-semibold text-blue-900 dark:text-blue-200">Pre-thesis context</h2>
          </div>
          <button
            type="button"
            onClick={loadContext}
            disabled={!form.ticker.trim() || context.isPending}
            className="px-3 py-1.5 text-sm bg-blue-700 dark:bg-blue-300 text-white dark:text-blue-950 rounded-md font-medium hover:opacity-90 disabled:opacity-50 flex items-center gap-2"
          >
            {context.isPending ? (
              <>
                <RefreshCw size={14} className="animate-spin" />
                Loading...
              </>
            ) : (
              <>Load context for {form.ticker || "ticker"}</>
            )}
          </button>
        </div>
        <p className="text-xs text-blue-700 dark:text-blue-300 mt-1">
          Surfaces recent news, your saved analyses, and matching sector principles from the knowledge base — before you commit your thesis.
        </p>
        {context.error && (
          <div className="mt-3 text-sm text-red-700 dark:text-red-400 flex items-start gap-2">
            <AlertCircle size={14} className="mt-0.5 shrink-0" />
            <span>{context.error.message}</span>
          </div>
        )}
        {context.data && (
          <div className="mt-4 max-h-[400px] overflow-y-auto bg-white dark:bg-slate-900 rounded-md border border-blue-200 dark:border-blue-900 p-4">
            <MarkdownBlock text={context.data.text} />
          </div>
        )}
      </section>

      <form
        className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm grid md:grid-cols-2 gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          mutation.mutate();
        }}
      >
        <Field label="Ticker">
          <input value={form.ticker} onChange={update("ticker")} className="input" required />
        </Field>

        <Field label="Conviction">
          <select value={form.conviction} onChange={update("conviction")} className="input">
            <option>Low</option>
            <option>Medium</option>
            <option>High</option>
          </select>
        </Field>

        <Field label="Entry / Buy price (VND)">
          <input
            type="number"
            value={form.buy_price}
            onChange={update("buy_price")}
            className="input"
            required
          />
        </Field>

        <Field label="12-month target (VND)">
          <input
            type="number"
            value={form.target_price}
            onChange={update("target_price")}
            className="input"
            required
          />
        </Field>

        <Field label="Stop-loss (VND)" hint="Where the thesis is broken — exit immediately">
          <input
            type="number"
            value={form.stop_price}
            onChange={update("stop_price")}
            className="input"
            required
          />
        </Field>

        <div />

        <Field label="Core thesis" full hint="Why are you buying? What's the edge?">
          <textarea
            value={form.thesis}
            onChange={update("thesis")}
            className="input"
            rows={4}
            required
          />
        </Field>

        <Field
          label="Falsification criteria"
          full
          hint="Specific, testable conditions that exit the trade immediately"
        >
          <textarea
            value={form.falsification_criteria}
            onChange={update("falsification_criteria")}
            className="input"
            rows={4}
            required
            placeholder="e.g.&#10;1. Core IT revenue growth <10% for 2 consecutive quarters&#10;2. Net margin compresses below 8%"
          />
        </Field>

        <Field label="Catalysts" hint="2-3 upcoming events that could prove the thesis">
          <textarea
            value={form.catalysts}
            onChange={update("catalysts")}
            className="input"
            rows={3}
          />
        </Field>

        <Field
          label="Strongest bias (pre-mortem)"
          hint="Which cognitive bias is most likely affecting this thesis?"
        >
          <textarea
            value={form.strongest_bias}
            onChange={update("strongest_bias")}
            className="input"
            rows={3}
            placeholder="e.g. confirmation bias — I've been bullish for years"
          />
        </Field>

        <Field
          label="If wrong in 12 months..."
          full
          hint="What's the SINGLE most likely reason this fails?"
        >
          <textarea
            value={form.premortem_reason}
            onChange={update("premortem_reason")}
            className="input"
            rows={3}
            placeholder="e.g. US/EU IT services demand slowdown reduces FPT's overseas revenue 15-20%"
          />
        </Field>

        <div className="md:col-span-2 pt-2">
          <button
            type="submit"
            disabled={!canSubmit || mutation.isPending}
            className="w-full md:w-auto px-6 py-2 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded-md font-medium hover:opacity-90 disabled:opacity-50 flex items-center gap-2 justify-center"
          >
            {mutation.isPending ? (
              <>
                <RefreshCw size={16} className="animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <FileText size={16} />
                Save thesis
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
        <section className="bg-green-50 dark:bg-green-950/50 border border-green-300 dark:border-green-800 rounded-lg p-6 shadow-sm">
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
  full,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
  full?: boolean;
}) {
  return (
    <label className={`block ${full ? "md:col-span-2" : ""}`}>
      <span className="block text-sm font-medium mb-1">{label}</span>
      {children}
      {hint && <span className="block text-xs text-slate-500 mt-1">{hint}</span>}
    </label>
  );
}
