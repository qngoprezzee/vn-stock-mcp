"use client";

import { useQuery } from "@tanstack/react-query";
import { marketOverview, economyNews, apiHealth } from "@/lib/api";
import { MarkdownBlock } from "@/components/MarkdownBlock";
import { Activity, AlertCircle, RefreshCw } from "lucide-react";

export default function Dashboard() {
  const health = useQuery({ queryKey: ["health"], queryFn: apiHealth, retry: 0 });
  const market = useQuery({
    queryKey: ["market-overview"],
    queryFn: marketOverview,
    enabled: health.data?.status === "ok",
  });
  const news = useQuery({
    queryKey: ["economy-news"],
    queryFn: () => economyNews(10),
    enabled: health.data?.status === "ok",
  });

  if (health.isLoading) return <Loading />;
  if (health.isError || health.data?.status !== "ok") return <ApiOffline />;

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">Today&apos;s Market</h1>
        <p className="text-slate-600 dark:text-slate-400 mt-1">
          Start every session with a market pulse and economy headlines.
        </p>
      </header>

      <div className="grid lg:grid-cols-2 gap-6">
        <Card title="Market Overview" loading={market.isLoading} error={market.error}>
          {market.data && <MarkdownBlock text={market.data.text} />}
        </Card>

        <Card title="Economy Headlines" loading={news.isLoading} error={news.error}>
          {news.data && <MarkdownBlock text={news.data.text} />}
        </Card>
      </div>
    </div>
  );
}

function Card({
  title,
  children,
  loading,
  error,
}: {
  title: string;
  children: React.ReactNode;
  loading: boolean;
  error: Error | null;
}) {
  return (
    <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm">
      <h2 className="text-lg font-semibold mb-4">{title}</h2>
      {loading && (
        <div className="flex items-center gap-2 text-slate-500">
          <RefreshCw className="animate-spin" size={16} />
          Loading...
        </div>
      )}
      {error && (
        <div className="flex items-start gap-2 text-red-700 dark:text-red-400 text-sm">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>{error.message}</span>
        </div>
      )}
      {!loading && !error && children}
    </section>
  );
}

function Loading() {
  return (
    <div className="flex items-center gap-2 text-slate-500">
      <RefreshCw className="animate-spin" size={16} />
      Connecting to API...
    </div>
  );
}

function ApiOffline() {
  return (
    <div className="rounded-lg border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/50 p-6">
      <div className="flex items-center gap-2 text-amber-800 dark:text-amber-200 font-semibold mb-2">
        <Activity size={18} />
        API not reachable
      </div>
      <p className="text-sm text-amber-700 dark:text-amber-300 mb-3">
        The FastAPI backend at{" "}
        <code className="px-1 bg-white/50 dark:bg-black/30 rounded">http://127.0.0.1:8000</code>{" "}
        is not responding.
      </p>
      <p className="text-sm text-amber-700 dark:text-amber-300">From the project root, run:</p>
      <pre className="mt-2 text-xs bg-white/70 dark:bg-black/30 p-2 rounded overflow-x-auto">
        .venv/bin/python -m uvicorn api:app --reload
      </pre>
    </div>
  );
}
