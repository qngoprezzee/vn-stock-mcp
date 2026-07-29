"use client";

import { useMemo, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { ExternalLink, RefreshCw, Rss, Search, Globe2, Sparkles, ChevronUp, ChevronDown } from "lucide-react";
import { marketNewsData, marketNewsDigest, type NewsArticle, type NewsDigestResponse } from "@/lib/api";
import { MarkdownBlock } from "@/components/MarkdownBlock";

type LangFilter = "all" | "vi" | "en";

const isEnglishSource = (source: string) => source.startsWith("Stockbiz EN");

function sourceColor(source: string): string {
  if (source.startsWith("Stockbiz")) return "bg-amber-600/20 text-amber-300 border-amber-700/40";
  if (source.startsWith("CafeF"))    return "bg-emerald-600/20 text-emerald-300 border-emerald-700/40";
  if (source.startsWith("VnEconomy")) return "bg-sky-600/20 text-sky-300 border-sky-700/40";
  if (source.startsWith("Báo Đầu tư")) return "bg-rose-600/20 text-rose-300 border-rose-700/40";
  if (source.startsWith("VnExpress")) return "bg-indigo-600/20 text-indigo-300 border-indigo-700/40";
  if (source.startsWith("Vietnam Inv")) return "bg-purple-600/20 text-purple-300 border-purple-700/40";
  if (source.startsWith("Tin Nhanh"))  return "bg-cyan-600/20 text-cyan-300 border-cyan-700/40";
  return "bg-slate-700/40 text-slate-300 border-slate-600/40";
}

export default function NewsPage() {
  const [lang, setLang]       = useState<LangFilter>("all");
  const [search, setSearch]   = useState("");
  const [sourceFilter, setSourceFilter] = useState<string>("");
  const [digestOpen, setDigestOpen] = useState(true);

  const { data, isLoading, error, refetch, isRefetching } = useQuery({
    queryKey: ["market-news-data"],
    queryFn:  () => marketNewsData(80),
    staleTime: 5 * 60 * 1000,
  });

  const digest = useMutation<NewsDigestResponse, Error, { force?: boolean }>({
    mutationFn: ({ force }) => marketNewsDigest({ limit: 60, force }),
  });

  const articles: NewsArticle[] = data?.articles ?? [];

  const sources = useMemo(() => {
    const set = new Set<string>();
    articles.forEach(a => set.add(a.source));
    return Array.from(set).sort();
  }, [articles]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return articles.filter(a => {
      if (lang === "vi" && isEnglishSource(a.source)) return false;
      if (lang === "en" && !isEnglishSource(a.source)) return false;
      if (sourceFilter && a.source !== sourceFilter) return false;
      if (q && !a.title.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [articles, lang, sourceFilter, search]);

  const counts = useMemo(() => {
    const en = articles.filter(a => isEnglishSource(a.source)).length;
    return { all: articles.length, en, vi: articles.length - en };
  }, [articles]);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Headlines</h1>
          <p className="text-slate-400 text-sm mt-1">
            Vietnam economy & market news aggregated from {data?.sources_total ?? 15} RSS sources, including English coverage from Stockbiz.
            {data?.generated_at && <span className="ml-2 text-slate-500">· {data.generated_at}</span>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => digest.mutate({ force: !!digest.data })}
            disabled={digest.isPending || isLoading || !articles.length}
            className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white text-sm font-medium transition-colors"
          >
            {digest.isPending
              ? <RefreshCw size={14} className="animate-spin" />
              : <Sparkles size={14} />}
            {digest.isPending
              ? "Synthesizing…"
              : digest.data ? "Regenerate Digest" : "AI Digest"}
          </button>
          <button
            onClick={() => refetch()}
            disabled={isRefetching}
            className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-slate-700 text-sm text-slate-300 hover:text-white hover:border-slate-500 disabled:opacity-50 transition-colors"
          >
            <RefreshCw size={14} className={isRefetching ? "animate-spin" : ""} />
            {isRefetching ? "Refreshing" : "Refresh"}
          </button>
        </div>
      </header>

      {/* AI Digest panel */}
      {digest.isPending && !digest.data && (
        <div className="flex items-center gap-2 text-purple-300 text-sm bg-purple-900/15 border border-purple-800/40 rounded-lg px-4 py-3">
          <RefreshCw size={14} className="animate-spin" />
          Reading {data?.total ?? 0} headlines and writing a one-page digest…
        </div>
      )}

      {digest.error && (
        <div className="text-red-400 text-sm bg-red-900/15 border border-red-800/40 rounded-lg px-4 py-3">
          Digest failed: {digest.error.message}
          {digest.error.message.includes("OPENAI_API_KEY") && (
            <span className="block text-xs mt-1 text-red-300/80">Set OPENAI_API_KEY in the api.py environment and restart.</span>
          )}
        </div>
      )}

      {digest.data && (
        <section className="bg-gradient-to-br from-purple-900/20 to-slate-900/60 border border-purple-700/40 rounded-xl overflow-hidden">
          <button
            onClick={() => setDigestOpen(o => !o)}
            className="w-full flex items-center justify-between px-5 py-3 text-left hover:bg-purple-900/10 transition-colors"
          >
            <div className="flex items-center gap-2">
              <Sparkles size={15} className="text-purple-300" />
              <span className="text-sm font-semibold text-purple-200">AI Daily Digest</span>
              <span className="text-[11px] text-slate-500">
                · {digest.data.article_count} headlines · {digest.data.generated_at} · {digest.data.model}
              </span>
            </div>
            {digestOpen
              ? <ChevronUp size={15} className="text-slate-400" />
              : <ChevronDown size={15} className="text-slate-400" />}
          </button>
          {digestOpen && (
            <div className="px-5 pb-5 pt-1 border-t border-purple-800/30">
              <div className="prose prose-invert prose-sm max-w-none text-slate-200">
                <MarkdownBlock text={digest.data.digest} />
              </div>
            </div>
          )}
        </section>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-center">
        <div className="flex gap-1 p-1 bg-slate-900 border border-slate-800 rounded-lg">
          {([
            ["all", "All",        counts.all, Rss],
            ["vi",  "Vietnamese", counts.vi,  Globe2],
            ["en",  "English",    counts.en,  Globe2],
          ] as const).map(([id, label, count, Icon]) => (
            <button
              key={id}
              onClick={() => setLang(id as LangFilter)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                lang === id
                  ? "bg-slate-700 text-white"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <Icon size={12} />
              {label}
              <span className="text-[10px] opacity-60">{count}</span>
            </button>
          ))}
        </div>

        <select
          value={sourceFilter}
          onChange={e => setSourceFilter(e.target.value)}
          className="px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-md text-xs text-slate-300 focus:outline-none focus:border-slate-600"
        >
          <option value="">All sources</option>
          {sources.map(s => <option key={s} value={s}>{s}</option>)}
        </select>

        <div className="flex items-center gap-2 flex-1 min-w-[200px] max-w-md px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-md focus-within:border-slate-600">
          <Search size={13} className="text-slate-500" />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Filter headlines…"
            className="bg-transparent flex-1 text-xs text-slate-200 placeholder-slate-500 focus:outline-none"
          />
        </div>

        <span className="text-xs text-slate-500 ml-auto">
          {filtered.length} of {articles.length}
        </span>
      </div>

      {/* Content */}
      {isLoading && (
        <div className="flex items-center gap-2 text-slate-500 py-12 justify-center">
          <RefreshCw size={15} className="animate-spin" /> Loading headlines…
        </div>
      )}

      {error && (
        <p className="text-red-400 text-sm py-8">Failed to load news.</p>
      )}

      {!isLoading && !error && filtered.length === 0 && (
        <div className="text-slate-400 text-sm py-12 text-center">
          No headlines match your filter.
        </div>
      )}

      <div className="space-y-2">
        {filtered.map(a => (
          <a
            key={`${a.source}::${a.link}::${a.title}`}
            href={a.link}
            target="_blank"
            rel="noreferrer"
            className="group block bg-slate-900/60 border border-slate-800 rounded-lg p-4 hover:border-slate-600 hover:bg-slate-900 transition-colors"
          >
            <div className="flex items-start gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap mb-1.5">
                  <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded border ${sourceColor(a.source)}`}>
                    {a.source}
                  </span>
                  {a.date && <span className="text-[11px] text-slate-500">{a.date}</span>}
                  {isEnglishSource(a.source) && (
                    <span className="text-[10px] text-amber-400/80 uppercase tracking-wider">EN</span>
                  )}
                </div>
                <p className="text-sm text-slate-200 leading-snug group-hover:text-white">
                  {a.title}
                </p>
                {a.summary && (
                  <p className="text-xs text-slate-400 leading-relaxed mt-1.5 line-clamp-3 group-hover:text-slate-300">
                    {a.summary}
                  </p>
                )}
              </div>
              <ExternalLink size={14} className="text-slate-600 group-hover:text-slate-300 shrink-0 mt-0.5" />
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
