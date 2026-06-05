"use client";

import { useState, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  FileText, Upload, Link as LinkIcon, ExternalLink,
  RefreshCw, CheckCircle, AlertCircle, BookOpen, ChevronDown, ChevronRight,
  Rss, Download, Sparkles,
} from "lucide-react";
import { MarkdownBlock } from "@/components/MarkdownBlock";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

// ── Broker portals ─────────────────────────────────────────────────────────────
const BROKERS = [
  { name: "TCBS",        url: "https://tcinvest.tcbs.com.vn/phan-tich-bao-cao",  color: "bg-blue-600" },
  { name: "Mirae Asset", url: "https://www.miraeasset.com.vn/vi/research",        color: "bg-red-600" },
  { name: "VCBS",        url: "https://www.vcbs.com.vn/vi/nghien-cuu",            color: "bg-green-700" },
  { name: "VietCap",     url: "https://vietcap.com.vn/vi/research-center",        color: "bg-orange-600" },
  { name: "HSC",         url: "https://hsc.com.vn/vi/research-report",            color: "bg-indigo-600" },
  { name: "SSI",         url: "https://www.ssi.com.vn/vi/nghien-cuu",             color: "bg-teal-600" },
  { name: "VNDIRECT",    url: "https://dstock.vndirect.com.vn/research",           color: "bg-purple-600" },
  { name: "MBS",         url: "https://www.mbs.com.vn/vi/nghien-cuu",             color: "bg-yellow-600" },
];

// ── API calls ─────────────────────────────────────────────────────────────────
type Analysis = { filename: string; title: string; ticker: string; date: string; size_kb: number };
type CorpusReport = { id: string; title: string; source: string; url: string; pub_date: string; ingested_at: string; tickers: string[]; category: string; language: string };

const fetchJSON = (path: string) => fetch(`${API}${path}`).then(r => r.json());

// ── Tabs ──────────────────────────────────────────────────────────────────────
type Tab = "feed" | "import" | "analyses" | "corpus";

export default function ReportsPage() {
  const [tab, setTab] = useState<Tab>("feed");

  const tabs: { id: Tab; label: string; icon: React.ElementType }[] = [
    { id: "feed",     label: "Live Feed",           icon: Rss },
    { id: "import",   label: "Import Report",       icon: Upload },
    { id: "analyses", label: "Saved Analyses",      icon: FileText },
    { id: "corpus",   label: "Knowledge Corpus",    icon: BookOpen },
  ];

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">Research Reports</h1>
        <p className="text-slate-400 text-sm mt-1">
          Import broker research PDFs or URLs into your knowledge base, then browse saved analyses.
        </p>
      </header>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-slate-800">
        {tabs.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              tab === id
                ? "border-blue-500 text-blue-400"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            <Icon size={15} />
            {label}
          </button>
        ))}
      </div>

      {tab === "feed"     && <FeedTab />}
      {tab === "import"   && <ImportTab />}
      {tab === "analyses" && <AnalysesTab />}
      {tab === "corpus"   && <CorpusTab />}
    </div>
  );
}

// ── Report card with AI summary ───────────────────────────────────────────────
function ReportCard({ report: r, category }: { report: BrokerReport; category: string }) {
  const [expanded, setExpanded] = useState(false);
  const [summary, setSummary] = useState<string | null>(null);
  const [summarizing, setSummarizing] = useState(false);

  const handleSummarize = async () => {
    if (summary) { setExpanded(e => !e); return; }
    setSummarizing(true);
    setExpanded(true);
    try {
      const res = await fetch(`${API}/api/broker/summarize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: r.title_vi,
          broker: r.broker,
          date: r.date,
          category,
        }),
      });
      const d = await res.json();
      setSummary(d.summary || d.detail || "Could not generate summary.");
    } catch {
      setSummary("Summary failed — check OPENAI_API_KEY.");
    } finally {
      setSummarizing(false);
    }
  };

  return (
    <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden hover:border-slate-300 dark:hover:border-slate-700 transition-colors">
      <div className="p-4 flex items-start gap-4">
        <div className="w-9 h-9 rounded-lg bg-red-600 flex items-center justify-center text-white text-[10px] font-bold shrink-0">
          {r.broker_short.slice(0, 3)}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-slate-800 dark:text-slate-200 leading-snug">{r.title_vi}</p>
          {r.title_en && r.title_en !== r.title_vi && (
            <p className="text-xs text-slate-400 mt-0.5 italic">{r.title_en}</p>
          )}
          <div className="flex items-center gap-3 mt-1.5 flex-wrap">
            <span className="text-xs text-slate-400">{r.date}</span>
            <span className="text-xs px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-500">{r.broker_short}</span>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            onClick={handleSummarize}
            title="AI summary"
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-slate-200 dark:border-slate-700 text-slate-500 hover:text-purple-600 hover:border-purple-400 text-xs transition-colors"
          >
            {summarizing
              ? <RefreshCw size={12} className="animate-spin" />
              : <Sparkles size={12} />}
            {summary && !summarizing ? (expanded ? "Hide" : "Summary") : "Summarize"}
          </button>
          {r.pdf_url && (
            <a href={r.pdf_url} target="_blank" rel="noreferrer"
              title="PDF (requires MASVN login)"
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-slate-200 dark:border-slate-700 text-slate-500 hover:text-slate-800 dark:hover:text-white text-xs transition-colors">
              <Download size={12} /> PDF
            </a>
          )}
          <a href={r.page_url} target="_blank" rel="noreferrer"
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs transition-colors">
            <ExternalLink size={12} /> Read
          </a>
        </div>
      </div>

      {/* AI Summary panel */}
      {expanded && (
        <div className="border-t border-slate-100 dark:border-slate-800 px-4 py-3 bg-purple-50 dark:bg-purple-900/10">
          {summarizing ? (
            <div className="flex items-center gap-2 text-slate-500 text-xs">
              <RefreshCw size={12} className="animate-spin" /> Generating summary…
            </div>
          ) : (
            <div>
              <div className="flex items-center gap-1.5 mb-1.5">
                <Sparkles size={12} className="text-purple-500" />
                <span className="text-xs font-semibold text-purple-600 dark:text-purple-400">AI Summary</span>
                <span className="text-[10px] text-slate-400 ml-1">from title · not a substitute for the full report</span>
              </div>
              <p className="text-sm text-slate-700 dark:text-slate-300 leading-relaxed">{summary}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}


// ── Live Feed tab ─────────────────────────────────────────────────────────────
type BrokerReport = {
  id: number; title_vi: string; title_en: string;
  date: string; pdf_url: string; page_url: string; thumbnail: string;
  broker: string; broker_short: string;
};

const FEED_SOURCES = [
  { broker: "masvn", category: "weekly", label: "MASVN Weekly Strategy" },
  { broker: "masvn", category: "daily",  label: "MASVN Daily Briefing" },
];

function FeedTab() {
  const [source, setSource] = useState(FEED_SOURCES[0]);

  const { data, isLoading, error } = useQuery({
    queryKey: ["broker-feed", source.broker, source.category],
    queryFn: () =>
      fetchJSON(`/api/broker/feed?broker=${source.broker}&category=${source.category}&limit=30`),
    staleTime: 30 * 60 * 1000,
  });

  const reports: BrokerReport[] = data?.reports ?? [];

  return (
    <div className="space-y-5">
      {/* Source selector */}
      <div className="flex gap-2 flex-wrap">
        {FEED_SOURCES.map(s => (
          <button
            key={`${s.broker}-${s.category}`}
            onClick={() => setSource(s)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors border ${
              source.broker === s.broker && source.category === s.category
                ? "bg-slate-700 border-slate-600 text-white"
                : "border-slate-800 text-slate-400 hover:text-white hover:border-slate-600"
            }`}
          >
            <Rss size={13} />
            {s.label}
          </button>
        ))}
        <span className="ml-auto text-xs text-slate-600 self-center">
          {data ? `${data.total} total · refreshed every 30 min` : ""}
        </span>
      </div>

      {/* Notice */}
      <div className="bg-slate-900/40 border border-slate-800 rounded-lg px-4 py-3 text-xs text-slate-400">
        <strong className="text-slate-300">Note:</strong> PDFs require login at MASVN. Click{" "}
        <span className="text-blue-400">Read on MASVN</span> to open the report page, or download
        the PDF and use <span className="text-blue-400">Import Report</span> to add it to your knowledge base.
      </div>

      {isLoading && <Spinner label="Fetching latest reports…" />}
      {error && <p className="text-red-400 text-sm">Failed to load feed.</p>}

      {/* Report list */}
      <div className="space-y-2">
        {reports.map(r => (
          <ReportCard key={r.id} report={r} category={source.category} />
        ))}
      </div>
    </div>
  );
}


// ── Import tab ────────────────────────────────────────────────────────────────
function ImportTab() {
  const qc = useQueryClient();
  const [mode, setMode] = useState<"url" | "pdf">("url");
  const [url, setUrl]     = useState("");
  const [source, setSource] = useState("");
  const [ticker, setTicker] = useState("");
  const [category, setCategory] = useState("filings");
  const [language, setLanguage] = useState("vi");
  const fileRef = useRef<HTMLInputElement>(null);
  const [log, setLog] = useState<string>("");

  const importUrl = useMutation({
    mutationFn: async () => {
      const r = await fetch(`${API}/api/reports/import-url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, source, ticker, category }),
      });
      return r.json();
    },
    onSuccess: (d) => {
      setLog(d.stdout + (d.stderr ? "\nSTDERR:\n" + d.stderr : ""));
      if (d.ok) qc.invalidateQueries({ queryKey: ["corpus-reports"] });
    },
  });

  const importPdf = useMutation({
    mutationFn: async () => {
      const file = fileRef.current?.files?.[0];
      if (!file) throw new Error("No file selected");
      const form = new FormData();
      form.append("file", file);
      form.append("source", source);
      form.append("ticker", ticker);
      form.append("category", category);
      form.append("language", language);
      const r = await fetch(`${API}/api/reports/import-pdf`, { method: "POST", body: form });
      return r.json();
    },
    onSuccess: (d) => {
      setLog(d.stdout + (d.stderr ? "\nSTDERR:\n" + d.stderr : ""));
      if (d.ok) qc.invalidateQueries({ queryKey: ["corpus-reports"] });
    },
  });

  const isPending = importUrl.isPending || importPdf.isPending;
  const result    = importUrl.data || importPdf.data;

  return (
    <div className="space-y-6">
      {/* Broker portals */}
      <section className="bg-slate-900/60 border border-slate-800 rounded-xl p-5">
        <h2 className="text-sm font-semibold text-slate-300 mb-4 uppercase tracking-wider">
          Broker Research Portals
        </h2>
        <p className="text-slate-500 text-xs mb-4">
          Download a research PDF from one of these portals, then import it below.
        </p>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {BROKERS.map(b => (
            <a
              key={b.name}
              href={b.url}
              target="_blank"
              rel="noreferrer"
              className={`flex items-center justify-between gap-2 px-3 py-2 rounded-lg text-white text-sm font-medium ${b.color} hover:opacity-90 transition-opacity`}
            >
              {b.name}
              <ExternalLink size={13} className="shrink-0 opacity-70" />
            </a>
          ))}
        </div>
      </section>

      {/* Import form */}
      <section className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 space-y-5">
        <div className="flex gap-2">
          {(["url", "pdf"] as const).map(m => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                mode === m ? "bg-slate-700 text-white" : "text-slate-400 hover:text-white"
              }`}
            >
              {m === "url" ? <LinkIcon size={14} /> : <Upload size={14} />}
              {m === "url" ? "Import by URL" : "Upload PDF"}
            </button>
          ))}
        </div>

        <div className="grid sm:grid-cols-2 gap-4">
          {mode === "url" ? (
            <div className="sm:col-span-2">
              <label className="block text-xs text-slate-400 mb-1">Report URL</label>
              <input
                value={url}
                onChange={e => setUrl(e.target.value)}
                placeholder="https://..."
                className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          ) : (
            <div className="sm:col-span-2">
              <label className="block text-xs text-slate-400 mb-1">PDF File</label>
              <input
                ref={fileRef}
                type="file"
                accept=".pdf"
                className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-400 file:mr-3 file:px-3 file:py-1 file:rounded file:border-0 file:bg-slate-600 file:text-white file:text-xs cursor-pointer"
              />
            </div>
          )}

          <div>
            <label className="block text-xs text-slate-400 mb-1">Source / Broker name</label>
            <input
              value={source}
              onChange={e => setSource(e.target.value)}
              placeholder="e.g. TCBS Research"
              className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Ticker (optional)</label>
            <input
              value={ticker}
              onChange={e => setTicker(e.target.value.toUpperCase())}
              placeholder="FPT"
              className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-100 font-mono uppercase focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Category</label>
            <select
              value={category}
              onChange={e => setCategory(e.target.value)}
              className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-100 focus:outline-none"
            >
              <option value="filings">Filings (broker/annual reports)</option>
              <option value="articles">Articles</option>
              <option value="papers">Research papers</option>
              <option value="books">Books</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Language</label>
            <select
              value={language}
              onChange={e => setLanguage(e.target.value)}
              className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-100 focus:outline-none"
            >
              <option value="vi">Vietnamese</option>
              <option value="en">English</option>
            </select>
          </div>
        </div>

        <button
          onClick={() => mode === "url" ? importUrl.mutate() : importPdf.mutate()}
          disabled={isPending || (mode === "url" && !url)}
          className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded-lg text-sm font-medium transition-colors"
        >
          {isPending ? <RefreshCw size={15} className="animate-spin" /> : <Upload size={15} />}
          {isPending ? "Importing…" : "Import"}
        </button>

        {/* Result log */}
        {result && (
          <div className={`rounded-lg p-4 text-xs font-mono border ${result.ok ? "border-emerald-800 bg-emerald-900/20 text-emerald-300" : "border-red-800 bg-red-900/20 text-red-300"}`}>
            <div className="flex items-center gap-2 mb-2 font-sans font-semibold">
              {result.ok ? <CheckCircle size={14} /> : <AlertCircle size={14} />}
              {result.ok ? "Import succeeded" : "Import failed"}
            </div>
            <pre className="whitespace-pre-wrap overflow-auto max-h-40">{log}</pre>
          </div>
        )}
      </section>
    </div>
  );
}

// ── Saved Analyses tab ────────────────────────────────────────────────────────
function AnalysesTab() {
  const { data, isLoading } = useQuery({
    queryKey: ["saved-analyses"],
    queryFn: () => fetchJSON("/api/reports/analyses"),
  });
  const [expanded, setExpanded] = useState<string | null>(null);
  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ["analysis-detail", expanded],
    queryFn: () => expanded ? fetchJSON(`/api/reports/analyses/${expanded}`) : null,
    enabled: !!expanded,
  });

  if (isLoading) return <Spinner label="Loading analyses…" />;
  const analyses: Analysis[] = data?.analyses ?? [];

  if (!analyses.length) return (
    <div className="text-slate-400 text-sm py-8 text-center">
      No saved analyses yet. Use <code className="bg-slate-800 px-1 rounded">save_analysis</code> in Claude to save a deep-dive.
    </div>
  );

  return (
    <div className="space-y-3">
      {analyses.map(a => (
        <div key={a.filename} className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-hidden">
          <button
            onClick={() => setExpanded(expanded === a.filename ? null : a.filename)}
            className="w-full flex items-center justify-between p-4 text-left hover:bg-slate-800/40 transition-colors"
          >
            <div className="flex items-center gap-3">
              <span className="font-mono font-bold text-blue-400 text-sm w-12">{a.ticker}</span>
              <div>
                <p className="text-sm font-medium text-slate-200">{a.title}</p>
                <p className="text-xs text-slate-500">{a.date} · {a.size_kb} KB</p>
              </div>
            </div>
            {expanded === a.filename ? <ChevronDown size={16} className="text-slate-400" /> : <ChevronRight size={16} className="text-slate-400" />}
          </button>

          {expanded === a.filename && (
            <div className="border-t border-slate-800 p-5">
              {detailLoading ? <Spinner label="Loading…" /> : (
                detail?.content ? (
                  <div className="prose prose-invert prose-sm max-w-none text-slate-300">
                    <MarkdownBlock text={detail.content} />
                  </div>
                ) : <p className="text-slate-500 text-sm">Could not load content.</p>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Knowledge Corpus tab ──────────────────────────────────────────────────────
function CorpusTab() {
  const { data, isLoading } = useQuery({
    queryKey: ["corpus-reports"],
    queryFn: () => fetchJSON("/api/reports/corpus"),
  });

  if (isLoading) return <Spinner label="Loading corpus…" />;
  const reports: CorpusReport[] = data?.reports ?? [];
  const total: number = data?.total ?? 0;

  return (
    <div className="space-y-4">
      <p className="text-slate-400 text-sm">{total} reports ingested (filings + papers)</p>
      {!reports.length && (
        <div className="text-slate-400 text-sm py-8 text-center">
          No reports in corpus yet. Import a broker PDF or URL to get started.
        </div>
      )}
      <div className="space-y-2">
        {reports.map(r => (
          <div key={r.id} className="bg-slate-900/60 border border-slate-800 rounded-lg p-4 flex items-start gap-4">
            <FileText size={16} className="text-slate-500 mt-0.5 shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <p className="text-sm font-medium text-slate-200 truncate">{r.title}</p>
                {r.tickers.map(t => (
                  <span key={t} className="text-xs px-1.5 py-0.5 rounded bg-slate-700 text-slate-300 font-mono">{t}</span>
                ))}
              </div>
              <div className="flex items-center gap-3 mt-1 flex-wrap">
                <span className="text-xs text-slate-500">{r.source}</span>
                {r.pub_date && <span className="text-xs text-slate-600">{r.pub_date.slice(0, 10)}</span>}
                <span className="text-xs text-slate-700">{r.language.toUpperCase()} · {r.category}</span>
              </div>
            </div>
            {r.url && (
              <a href={r.url} target="_blank" rel="noreferrer"
                className="text-slate-500 hover:text-slate-300 shrink-0">
                <ExternalLink size={14} />
              </a>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Shared ────────────────────────────────────────────────────────────────────
function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 text-slate-500 py-8 justify-center">
      <RefreshCw size={15} className="animate-spin" /> {label}
    </div>
  );
}
