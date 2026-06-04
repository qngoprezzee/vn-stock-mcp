"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function MarkdownBlock({ text }: { text: string }) {
  return (
    <div className="prose prose-slate dark:prose-invert max-w-none prose-table:text-sm prose-th:bg-slate-100 dark:prose-th:bg-slate-800 prose-th:px-3 prose-th:py-2 prose-td:px-3 prose-td:py-2 prose-th:border prose-td:border prose-th:border-slate-300 prose-td:border-slate-200 dark:prose-th:border-slate-700 dark:prose-td:border-slate-800">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}
