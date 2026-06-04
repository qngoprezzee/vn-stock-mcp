"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { knowledgeCompareAuthors } from "@/lib/api";
import { MarkdownBlock } from "@/components/MarkdownBlock";
import { Scale, RefreshCw, AlertCircle } from "lucide-react";

const KNOWN_AUTHORS = [
  "Warren Buffett",
  "Howard Marks",
  "Aswath Damodaran",
  "Michael Mauboussin",
];

const SAMPLE_TOPICS = [
  "cyclicality",
  "intrinsic value",
  "moat",
  "capital allocation",
  "earnings quality",
  "risk",
  "growth",
  "leverage",
];

export default function ComparePage() {
  const [topic, setTopic] = useState("cyclicality");
  const [authors, setAuthors] = useState<string[]>(["Warren Buffett", "Howard Marks"]);
  const [keywords, setKeywords] = useState("");
  const [contextParas, setContextParas] = useState("2");
  const [maxPerAuthor, setMaxPerAuthor] = useState("3");

  const mutation = useMutation({
    mutationFn: () =>
      knowledgeCompareAuthors({
        topic,
        authors,
        keywords: keywords.split(",").map((k) => k.trim()).filter(Boolean),
        context_paragraphs: Number(contextParas),
        max_per_author: Number(maxPerAuthor),
      }),
  });

  const toggleAuthor = (a: string) => {
    setAuthors((prev) =>
      prev.includes(a) ? prev.filter((x) => x !== a) : [...prev, a]
    );
  };

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
          <Scale size={28} />
          Cross-Reference Engine
        </h1>
        <p className="text-slate-600 dark:text-slate-400 mt-1">
          Pull passages from multiple investing authors on the same topic — surface where they actually disagree.
        </p>
      </header>

      <form
        className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          mutation.mutate();
        }}
      >
        <div>
          <label htmlFor="topic" className="block text-sm font-medium mb-1">Topic</label>
          <input
            id="topic"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="e.g. cyclicality"
            className="w-full px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-md bg-white dark:bg-slate-800"
            required
          />
          <div className="mt-2 flex flex-wrap gap-2">
            {SAMPLE_TOPICS.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTopic(t)}
                className="text-xs px-2 py-1 rounded border border-slate-300 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800"
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium mb-2">Authors to compare</label>
          <div className="flex flex-wrap gap-2">
            {KNOWN_AUTHORS.map((a) => {
              const checked = authors.includes(a);
              return (
                <button
                  key={a}
                  type="button"
                  onClick={() => toggleAuthor(a)}
                  className={`px-3 py-1.5 rounded-md text-sm border transition-colors ${
                    checked
                      ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900 border-slate-900 dark:border-slate-100"
                      : "border-slate-300 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800"
                  }`}
                >
                  {a}
                </button>
              );
            })}
          </div>
          <p className="text-xs text-slate-500 mt-1">
            Select at least one. Authors must exist in the knowledge corpus.
          </p>
        </div>

        <div className="grid md:grid-cols-3 gap-4">
          <div>
            <label htmlFor="keywords" className="block text-sm font-medium mb-1">Extra keywords (optional)</label>
            <input
              id="keywords"
              value={keywords}
              onChange={(e) => setKeywords(e.target.value)}
              placeholder="comma-separated"
              className="w-full px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-md bg-white dark:bg-slate-800"
            />
          </div>
          <div>
            <label htmlFor="ctx" className="block text-sm font-medium mb-1">Context paragraphs</label>
            <input
              id="ctx"
              type="number"
              min="1"
              max="5"
              value={contextParas}
              onChange={(e) => setContextParas(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-md bg-white dark:bg-slate-800"
            />
          </div>
          <div>
            <label htmlFor="max" className="block text-sm font-medium mb-1">Max passages/author</label>
            <input
              id="max"
              type="number"
              min="1"
              max="10"
              value={maxPerAuthor}
              onChange={(e) => setMaxPerAuthor(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-md bg-white dark:bg-slate-800"
            />
          </div>
        </div>

        <button
          type="submit"
          disabled={mutation.isPending || authors.length === 0}
          className="px-4 py-2 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded-md font-medium hover:opacity-90 disabled:opacity-50 flex items-center gap-2"
        >
          {mutation.isPending ? (
            <>
              <RefreshCw size={16} className="animate-spin" />
              Searching corpus...
            </>
          ) : (
            <>
              <Scale size={16} />
              Compare
            </>
          )}
        </button>
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
          <div className="mt-6 pt-4 border-t border-slate-200 dark:border-slate-800">
            <p className="text-sm text-slate-500">
              <strong>Next:</strong> ask Claude Code to synthesize this into a comparison page. Use{" "}
              <code className="px-1 bg-slate-100 dark:bg-slate-800 rounded">vn-comparative-research</code>{" "}
              skill — it writes the final &quot;where they agree / where they differ / VN application&quot; analysis to{" "}
              <code className="px-1 bg-slate-100 dark:bg-slate-800 rounded">
                knowledge/wiki/comparisons/{topic.toLowerCase().replace(/[^a-z0-9]+/g, "-")}.md
              </code>.
            </p>
          </div>
        </section>
      )}
    </div>
  );
}
