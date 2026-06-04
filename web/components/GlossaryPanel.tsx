"use client";

import { useQuery } from "@tanstack/react-query";
import { knowledgeGlossary, type GlossaryConcept } from "@/lib/api";
import { BookOpen, AlertCircle, Quote } from "lucide-react";

/**
 * Collapsible panel that explains the concepts behind a calculation.
 * Pass `conceptKeys` to filter which concepts to show (e.g. on the valuation
 * page, ["dcf", "wacc", "fcf", "intrinsic_value", "pe_ratio", "pb_ratio",
 * "ev_ebitda", "margin_of_safety", "sensitivity_grid", "triangulation",
 * "peer_relative"]).
 */
export function GlossaryPanel({
  title = "What's behind these numbers?",
  conceptKeys,
}: {
  title?: string;
  conceptKeys?: string[];
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["glossary"],
    queryFn: knowledgeGlossary,
    staleTime: 60 * 60 * 1000, // 1 hour — glossary doesn't change often
  });

  if (isLoading) return null;
  if (error || !data) return null;

  const allConcepts = Object.entries(data.concepts);
  const shown = conceptKeys
    ? allConcepts.filter(([key]) => conceptKeys.includes(key))
    : allConcepts;

  if (shown.length === 0) return null;

  return (
    <details className="bg-amber-50/50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-900 rounded-lg shadow-sm group">
      <summary className="cursor-pointer p-5 flex items-center gap-2 select-none">
        <BookOpen size={18} className="text-amber-700 dark:text-amber-300 shrink-0" />
        <span className="font-semibold text-amber-900 dark:text-amber-200">
          📖 {title}
        </span>
        <span className="ml-auto text-xs text-amber-700 dark:text-amber-300 group-open:hidden">
          ({shown.length} concept{shown.length === 1 ? "" : "s"} — click to expand)
        </span>
      </summary>
      <div className="px-5 pb-5 space-y-5">
        {shown.map(([key, c]) => (
          <ConceptCard key={key} concept={c} />
        ))}
      </div>
    </details>
  );
}

function ConceptCard({ concept: c }: { concept: GlossaryConcept }) {
  return (
    <div className="bg-white dark:bg-slate-900 rounded-md border border-slate-200 dark:border-slate-800 p-4">
      <h3 className="font-semibold text-slate-900 dark:text-slate-100 mb-1">
        {c.name}
      </h3>
      <p className="text-sm text-slate-700 dark:text-slate-300 mb-3">
        {c.definition}
      </p>

      {c.formula && (
        <div className="mb-3 p-2 bg-slate-50 dark:bg-slate-800 rounded font-mono text-xs text-slate-800 dark:text-slate-200 overflow-x-auto">
          {c.formula}
        </div>
      )}

      {c.key_quote && (
        <blockquote className="mb-3 pl-3 border-l-2 border-slate-300 dark:border-slate-700 text-sm text-slate-600 dark:text-slate-400">
          <div className="flex items-start gap-2">
            <Quote size={14} className="mt-0.5 shrink-0 text-slate-400" />
            <div>
              <span className="italic">&ldquo;{c.key_quote.text}&rdquo;</span>
              <div className="text-xs text-slate-500 mt-1">
                — {c.key_quote.author}, <span className="opacity-75">{c.key_quote.context}</span>
              </div>
            </div>
          </div>
        </blockquote>
      )}

      {c.when_to_use && (
        <div className="mb-3 text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-300">When to use: </span>
          <span className="text-slate-600 dark:text-slate-400">{c.when_to_use}</span>
        </div>
      )}

      {c.common_pitfalls && c.common_pitfalls.length > 0 && (
        <div className="text-sm">
          <div className="flex items-center gap-1 font-medium text-slate-700 dark:text-slate-300 mb-1">
            <AlertCircle size={12} className="text-amber-600" />
            Common pitfalls
          </div>
          <ul className="list-disc pl-5 space-y-0.5 text-slate-600 dark:text-slate-400">
            {c.common_pitfalls.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
