"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { knowledgeBriefRead, knowledgeBriefGather, knowledgeCorpusStats } from "@/lib/api";
import { MarkdownBlock } from "@/components/MarkdownBlock";
import { Sun, RefreshCw, AlertCircle, FileText, Sparkles, BookOpen } from "lucide-react";

function todayISO(): string {
  const d = new Date();
  const offsetMs = 7 * 60 * 60 * 1000; // VN_TZ
  const vnDate = new Date(d.getTime() + offsetMs);
  return vnDate.toISOString().slice(0, 10);
}

export default function BriefPage() {
  const [date, setDate] = useState<string>(todayISO());
  const queryClient = useQueryClient();

  const corpusStats = useQuery({
    queryKey: ["corpus-stats"],
    queryFn: knowledgeCorpusStats,
  });

  const brief = useQuery({
    queryKey: ["brief", date],
    queryFn: () => knowledgeBriefRead(date),
  });

  const gather = useMutation({
    mutationFn: () => knowledgeBriefGather(date),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["brief", date] }),
  });

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
          <Sun size={28} className="text-amber-500" />
          Morning Brief
        </h1>
        <p className="text-slate-600 dark:text-slate-400 mt-1">
          Daily curated briefing from the knowledge corpus. Two-stage flow: gather inputs → synthesize in Claude Code.
        </p>
      </header>

      {corpusStats.data && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="Total sources" value={corpusStats.data.total.toLocaleString()} />
          <StatCard label="Articles" value={String(corpusStats.data.by_category.articles ?? 0)} />
          <StatCard label="Books" value={String(corpusStats.data.by_category.books ?? 0)} />
          <StatCard label="Blogs & Papers"
            value={String((corpusStats.data.by_category.blogs ?? 0) + (corpusStats.data.by_category.papers ?? 0))} />
        </div>
      )}

      <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm">
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label htmlFor="date" className="block text-sm font-medium mb-1">Date</label>
            <input
              id="date"
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              max={todayISO()}
              className="px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-md bg-white dark:bg-slate-800"
            />
          </div>
          <button
            onClick={() => gather.mutate()}
            disabled={gather.isPending}
            className="px-4 py-2 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded-md font-medium hover:opacity-90 disabled:opacity-50 flex items-center gap-2"
          >
            {gather.isPending ? (
              <>
                <RefreshCw size={16} className="animate-spin" />
                Gathering...
              </>
            ) : (
              <>
                <Sparkles size={16} />
                Gather inputs
              </>
            )}
          </button>
        </div>

        {gather.error && (
          <div className="mt-4 flex items-start gap-2 text-red-700 dark:text-red-400 text-sm">
            <AlertCircle size={16} className="mt-0.5 shrink-0" />
            <span>{gather.error.message}</span>
          </div>
        )}
      </section>

      {brief.isLoading && (
        <p className="text-sm text-slate-500 flex items-center gap-2">
          <RefreshCw size={14} className="animate-spin" /> Loading brief...
        </p>
      )}

      {brief.data?.status === "missing" && !gather.isPending && (
        <div className="rounded-lg border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/50 p-6">
          <div className="flex items-center gap-2 text-amber-800 dark:text-amber-200 font-semibold mb-1">
            <FileText size={18} />
            No brief yet for {date}
          </div>
          <p className="text-sm text-amber-700 dark:text-amber-300">
            Click <strong>Gather inputs</strong> above to pull today&apos;s market overview, watchlist scan,
            recent articles, and a matching historical principle. After that, synthesize the brief in
            Claude Code with <code className="px-1 bg-white/50 dark:bg-black/30 rounded">/morning-brief</code>.
          </p>
        </div>
      )}

      {brief.data?.status === "pending" && (
        <section className="space-y-4">
          <div className="rounded-lg border border-blue-300 dark:border-blue-800 bg-blue-50 dark:bg-blue-950/50 p-6">
            <div className="flex items-center gap-2 text-blue-800 dark:text-blue-200 font-semibold mb-1">
              <BookOpen size={18} />
              Pending synthesis
            </div>
            <p className="text-sm text-blue-700 dark:text-blue-300 mb-3">
              Inputs gathered. Now open Claude Code and run <code className="px-1 bg-white/50 dark:bg-black/30 rounded">/morning-brief</code> (or invoke the <code className="px-1 bg-white/50 dark:bg-black/30 rounded">vn-morning-brief</code> skill).
              It will read this pending file and produce the final brief at <code>knowledge/briefs/{date}.md</code>.
            </p>
            <p className="text-xs text-blue-700 dark:text-blue-300">
              File path: <code>{brief.data.path}</code>
            </p>
          </div>

          <details className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm">
            <summary className="cursor-pointer font-semibold text-slate-700 dark:text-slate-200">
              View gathered inputs (raw)
            </summary>
            <div className="mt-4">
              <MarkdownBlock text={brief.data.content} />
            </div>
          </details>
        </section>
      )}

      {brief.data?.status === "synthesized" && (
        <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm">
          <MarkdownBlock text={brief.data.content} />
        </section>
      )}
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-4 shadow-sm">
      <div className="text-xs text-slate-500 uppercase tracking-wide">{label}</div>
      <div className="text-2xl font-semibold mt-1">{value}</div>
    </div>
  );
}
