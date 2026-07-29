"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  moneySupply, sectorRotation, marketCycle,
  loadMacroReport, listMacroReports, uploadMacroReport, brokerMacroFeed,
  m2SeriesRaw, m2SeriesManage,
  cpiSeriesRaw, cpiSeriesManage,
  rateSeriesRaw, rateSeriesManage,
  macroPillars,
} from "@/lib/api";
import { MarkdownBlock } from "@/components/MarkdownBlock";
import { Globe, RefreshCw, AlertCircle, Layers, Compass, Waves, FileText, Upload, Download, Cloud, Database, Plus, X, ExternalLink, Activity, TrendingUp, Percent } from "lucide-react";

type RankPeriod = "1M" | "3M" | "6M" | "YTD";

export default function MacroPage() {
  const qc = useQueryClient();
  const [rankBy, setRankBy] = useState<RankPeriod>("3M");

  const money = useMutation({ mutationFn: moneySupply });
  const rotation = useMutation({ mutationFn: () => sectorRotation(rankBy) });
  const cycle = useMutation({ mutationFn: marketCycle });

  // ── Macro Report Reader state ───────────────────────────────────────────
  const [reportUrl, setReportUrl] = useState("");
  const [reportBroker, setReportBroker] = useState("");
  const [reportTitle, setReportTitle] = useState("");
  const [reportSave, setReportSave] = useState(true);
  const [reportFile, setReportFile] = useState<File | null>(null);

  const loadReport = useMutation({
    mutationFn: () => reportFile
      ? uploadMacroReport({ file: reportFile, save: reportSave, broker: reportBroker, title: reportTitle })
      : loadMacroReport({ source: reportUrl, save: reportSave, broker: reportBroker, title: reportTitle }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["macro-reports-list"] });
    },
  });

  const reportsList = useQuery({
    queryKey: ["macro-reports-list"],
    queryFn: () => listMacroReports(20),
  });

  const brokerFeed = useQuery({
    queryKey: ["broker-macro-feed"],
    queryFn: () => brokerMacroFeed(10),
    staleTime: 30 * 60 * 1000, // 30 min
    enabled: false, // manually triggered
  });

  // M2 series state (Option A — user-managed monthly M2 from TradingView / SBV / GSO)
  const m2Series = useQuery({
    queryKey: ["m2-series-raw"],
    queryFn: m2SeriesRaw,
  });
  const [m2Date, setM2Date] = useState("");
  const [m2Value, setM2Value] = useState("");
  const [m2Source, setM2Source] = useState("TradingView ECONOMICS:VNM2");
  const [m2Note, setM2Note] = useState("");

  const addM2 = useMutation({
    mutationFn: () => m2SeriesManage({
      action: "add",
      date: m2Date,
      value_trillion_vnd: Number(m2Value),
      source: m2Source,
      note: m2Note,
    }),
    onSuccess: () => {
      setM2Date(""); setM2Value(""); setM2Note("");
      qc.invalidateQueries({ queryKey: ["m2-series-raw"] });
    },
  });

  const removeM2 = useMutation({
    mutationFn: (date: string) => m2SeriesManage({ action: "remove", date }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["m2-series-raw"] }),
  });

  const m2Rows = m2Series.data?.observations ?? [];
  const canAddM2 = m2Date.length >= 7 && Number(m2Value) > 0;

  // ── Macro Pillars (CPI + FX + Rate unified verdict) ─────────────────────
  const pillars = useMutation({ mutationFn: macroPillars });

  // CPI series state
  const cpiSeries = useQuery({
    queryKey: ["cpi-series-raw"],
    queryFn: cpiSeriesRaw,
  });
  const [cpiDate, setCpiDate] = useState("");
  const [cpiYoy, setCpiYoy] = useState("");
  const [cpiMom, setCpiMom] = useState("");
  const [cpiSource, setCpiSource] = useState("GSO monthly");
  const [cpiNote, setCpiNote] = useState("");

  const addCpi = useMutation({
    mutationFn: () => cpiSeriesManage({
      action: "add",
      date: cpiDate,
      cpi_yoy: Number(cpiYoy),
      cpi_mom: cpiMom ? Number(cpiMom) : undefined,
      source: cpiSource,
      note: cpiNote,
    }),
    onSuccess: () => {
      setCpiDate(""); setCpiYoy(""); setCpiMom(""); setCpiNote("");
      qc.invalidateQueries({ queryKey: ["cpi-series-raw"] });
    },
  });
  const removeCpi = useMutation({
    mutationFn: (date: string) => cpiSeriesManage({ action: "remove", date }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cpi-series-raw"] }),
  });
  const cpiRows = cpiSeries.data?.observations ?? [];
  const canAddCpi = cpiDate.length >= 7 && !Number.isNaN(Number(cpiYoy)) && cpiYoy !== "";

  // Rate series state
  const rateSeries = useQuery({
    queryKey: ["rate-series-raw"],
    queryFn: rateSeriesRaw,
  });
  const [rateDate, setRateDate] = useState("");
  const [rateRefi, setRateRefi] = useState("");
  const [rateInterbank, setRateInterbank] = useState("");
  const [rateDeposit, setRateDeposit] = useState("");
  const [rateSource, setRateSource] = useState("SBV");
  const [rateNote, setRateNote] = useState("");

  const addRate = useMutation({
    mutationFn: () => rateSeriesManage({
      action: "add",
      date: rateDate,
      refinance: Number(rateRefi),
      interbank_on: rateInterbank ? Number(rateInterbank) : undefined,
      deposit_12m: rateDeposit ? Number(rateDeposit) : undefined,
      source: rateSource,
      note: rateNote,
    }),
    onSuccess: () => {
      setRateDate(""); setRateRefi(""); setRateInterbank(""); setRateDeposit(""); setRateNote("");
      qc.invalidateQueries({ queryKey: ["rate-series-raw"] });
    },
  });
  const removeRate = useMutation({
    mutationFn: (date: string) => rateSeriesManage({ action: "remove", date }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rate-series-raw"] }),
  });
  const rateRows = rateSeries.data?.observations ?? [];
  const canAddRate = rateDate.length >= 7 && !Number.isNaN(Number(rateRefi)) && rateRefi !== "";

  const loadFromFeed = useMutation({
    mutationFn: (r: { pdf_url: string; title_vi: string }) =>
      loadMacroReport({
        source: r.pdf_url,
        save: true,
        broker: "Mirae Asset Securities Vietnam",
        title: r.title_vi,
        language: "vi",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["macro-reports-list"] }),
  });

  const canLoad = (reportUrl.trim().length > 0 || reportFile !== null);

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
          <Globe size={28} />
          Macro &amp; Cycle
        </h1>
        <p className="text-slate-600 dark:text-slate-400 mt-1">
          Vietnam money supply, sector rotation, and market cycle phase — read the tape before picking stocks.
        </p>
      </header>

      <section className="grid md:grid-cols-2 lg:grid-cols-4 gap-4">
        <AnalysisCard
          icon={<Activity size={18} />}
          title="Macro Pillars"
          hint="Unified verdict: CPI + USD/VND + interest rates → regime (Goldilocks / Reflation / Stagflation risk / Tight). Requires CPI + rate data below."
          onRun={() => pillars.mutate()}
          pending={pillars.isPending}
        />
        <AnalysisCard
          icon={<Waves size={18} />}
          title="Money Supply"
          hint="M2 growth, credit conditions, LOOSE/TIGHT verdict from top-5 bank credit growth."
          onRun={() => money.mutate()}
          pending={money.isPending}
        />
        <AnalysisCard
          icon={<Layers size={18} />}
          title="Sector Rotation"
          hint="Sector RS vs VN-Index. Cyclical vs defensive leadership regime."
          onRun={() => rotation.mutate()}
          pending={rotation.isPending}
          extra={
            <div className="mt-2">
              <label className="block text-xs text-slate-500 mb-1">Rank by</label>
              <select
                value={rankBy}
                onChange={(e) => setRankBy(e.target.value as RankPeriod)}
                className="w-full px-2 py-1 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-xs"
              >
                <option value="1M">1 Month</option>
                <option value="3M">3 Months</option>
                <option value="6M">6 Months</option>
                <option value="YTD">YTD</option>
              </select>
            </div>
          }
        />
        <AnalysisCard
          icon={<Compass size={18} />}
          title="Market Cycle"
          hint="Combined credit + trend + leadership → 8-phase classification with positioning."
          onRun={() => cycle.mutate()}
          pending={cycle.isPending}
        />
      </section>

      <section className="bg-amber-50/50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-900 rounded-md p-4 text-sm">
        <div className="flex items-start gap-2">
          <AlertCircle size={16} className="mt-0.5 shrink-0 text-amber-700 dark:text-amber-400" />
          <div className="text-amber-900 dark:text-amber-200">
            <strong>Read in order:</strong> Money Supply → Sector Rotation → Market Cycle. Each next tool
            builds on the prior. First run may take 30-60s (fetches ~40 tickers of price history).
            Cached 1 hour after first fetch.
          </div>
        </div>
      </section>

      {/* ── M2 Series Manager ────────────────────────────────────────────── */}
      <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Database size={20} />
            <h2 className="text-lg font-semibold">M2 Series (Manual Entry)</h2>
          </div>
          <a
            href="https://vn.tradingview.com/symbols/ECONOMICS-VNM2/"
            target="_blank"
            rel="noreferrer"
            className="text-xs text-slate-500 hover:text-slate-900 dark:hover:text-slate-100 inline-flex items-center gap-1"
          >
            TradingView ECONOMICS:VNM2 <ExternalLink size={12} />
          </a>
        </div>
        <p className="text-xs text-slate-500">
          VN monthly M2 has no free public API. Enter the latest value from TradingView, SBV, or GSO.
          Used by <code>Money Supply</code> as the freshest structural signal (higher priority than
          lagged WB annual data). Value in <strong>trillions VND</strong>.
        </p>

        {m2Rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b border-slate-200 dark:border-slate-800 text-slate-500">
                  <th className="py-2 px-2 font-medium">Month</th>
                  <th className="py-2 px-2 font-medium text-right">Value (T VND)</th>
                  <th className="py-2 px-2 font-medium">Source</th>
                  <th className="py-2 px-2 font-medium">Note</th>
                  <th className="py-2 px-2"></th>
                </tr>
              </thead>
              <tbody>
                {m2Rows.slice(-8).map((r) => (
                  <tr key={r.date} className="border-b border-slate-100 dark:border-slate-800/60">
                    <td className="py-2 px-2 font-mono">{r.date}</td>
                    <td className="py-2 px-2 text-right font-mono">{r.value_trillion_vnd.toLocaleString("vi-VN")}</td>
                    <td className="py-2 px-2 text-slate-500 text-xs">{r.source || "—"}</td>
                    <td className="py-2 px-2 text-slate-500 text-xs">{r.note || "—"}</td>
                    <td className="py-2 px-2 text-right">
                      <button
                        onClick={() => removeM2.mutate(r.date)}
                        disabled={removeM2.isPending}
                        className="p-1 rounded hover:bg-red-50 dark:hover:bg-red-950/40 text-red-600 disabled:opacity-40"
                      >
                        <X size={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {m2Rows.length > 8 && (
              <p className="text-xs text-slate-500 mt-2">Showing last 8 of {m2Rows.length} observations.</p>
            )}
          </div>
        )}

        <form
          className="grid grid-cols-2 md:grid-cols-5 gap-3 items-end pt-2 border-t border-slate-200 dark:border-slate-800"
          onSubmit={(e) => {
            e.preventDefault();
            if (canAddM2) addM2.mutate();
          }}
        >
          <div>
            <label className="block text-xs text-slate-500 mb-1">Month (YYYY-MM)</label>
            <input
              type="month"
              value={m2Date}
              onChange={(e) => setM2Date(e.target.value)}
              className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
              required
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Value (T VND)</label>
            <input
              type="number" step="1" min="1"
              value={m2Value}
              onChange={(e) => setM2Value(e.target.value)}
              placeholder="15200"
              className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
              required
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Source</label>
            <input
              value={m2Source}
              onChange={(e) => setM2Source(e.target.value)}
              className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Note (optional)</label>
            <input
              value={m2Note}
              onChange={(e) => setM2Note(e.target.value)}
              placeholder="e.g. revised, preliminary"
              className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
            />
          </div>
          <button
            type="submit"
            disabled={!canAddM2 || addM2.isPending}
            className="px-3 py-1.5 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded font-medium text-sm hover:opacity-90 disabled:opacity-40 flex items-center justify-center gap-1.5"
          >
            {addM2.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Plus size={14} />}
            Add / Update
          </button>
        </form>
        {addM2.error && <ErrorBanner message={addM2.error.message} />}
      </section>

      {/* ── CPI Series Manager ───────────────────────────────────────────── */}
      <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <TrendingUp size={20} />
            <h2 className="text-lg font-semibold">CPI Series — Lạm phát (Manual Entry)</h2>
          </div>
          <a
            href="https://vn.tradingview.com/symbols/ECONOMICS-VNCPIYY/"
            target="_blank"
            rel="noreferrer"
            className="text-xs text-slate-500 hover:text-slate-900 dark:hover:text-slate-100 inline-flex items-center gap-1"
          >
            TradingView ECONOMICS:VNCPIYY <ExternalLink size={12} />
          </a>
        </div>
        <p className="text-xs text-slate-500">
          Enter monthly CPI YoY from GSO (Tổng cục Thống kê) or TradingView. Mục tiêu Quốc hội 2026 ≤4.5%. Feeds <code>Macro Pillars</code>.
        </p>

        {cpiRows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b border-slate-200 dark:border-slate-800 text-slate-500">
                  <th className="py-2 px-2 font-medium">Month</th>
                  <th className="py-2 px-2 font-medium text-right">YoY %</th>
                  <th className="py-2 px-2 font-medium text-right">MoM %</th>
                  <th className="py-2 px-2 font-medium">Source</th>
                  <th className="py-2 px-2 font-medium">Note</th>
                  <th className="py-2 px-2"></th>
                </tr>
              </thead>
              <tbody>
                {cpiRows.slice(-8).map((r) => (
                  <tr key={r.date} className="border-b border-slate-100 dark:border-slate-800/60">
                    <td className="py-2 px-2 font-mono">{r.date}</td>
                    <td className="py-2 px-2 text-right font-mono">{r.cpi_yoy.toFixed(2)}</td>
                    <td className="py-2 px-2 text-right font-mono text-slate-500">
                      {r.cpi_mom != null ? r.cpi_mom.toFixed(2) : "—"}
                    </td>
                    <td className="py-2 px-2 text-slate-500 text-xs">{r.source || "—"}</td>
                    <td className="py-2 px-2 text-slate-500 text-xs">{r.note || "—"}</td>
                    <td className="py-2 px-2 text-right">
                      <button
                        onClick={() => removeCpi.mutate(r.date)}
                        disabled={removeCpi.isPending}
                        className="p-1 rounded hover:bg-red-50 dark:hover:bg-red-950/40 text-red-600 disabled:opacity-40"
                      >
                        <X size={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {cpiRows.length > 8 && (
              <p className="text-xs text-slate-500 mt-2">Showing last 8 of {cpiRows.length} observations.</p>
            )}
          </div>
        )}

        <form
          className="grid grid-cols-2 md:grid-cols-6 gap-3 items-end pt-2 border-t border-slate-200 dark:border-slate-800"
          onSubmit={(e) => {
            e.preventDefault();
            if (canAddCpi) addCpi.mutate();
          }}
        >
          <div>
            <label className="block text-xs text-slate-500 mb-1">Month</label>
            <input
              type="month"
              value={cpiDate}
              onChange={(e) => setCpiDate(e.target.value)}
              className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
              required
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">YoY %</label>
            <input
              type="number" step="0.01"
              value={cpiYoy}
              onChange={(e) => setCpiYoy(e.target.value)}
              placeholder="3.4"
              className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
              required
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">MoM % (optional)</label>
            <input
              type="number" step="0.01"
              value={cpiMom}
              onChange={(e) => setCpiMom(e.target.value)}
              placeholder="0.2"
              className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Source</label>
            <input
              value={cpiSource}
              onChange={(e) => setCpiSource(e.target.value)}
              className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Note</label>
            <input
              value={cpiNote}
              onChange={(e) => setCpiNote(e.target.value)}
              placeholder="e.g. revised"
              className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
            />
          </div>
          <button
            type="submit"
            disabled={!canAddCpi || addCpi.isPending}
            className="px-3 py-1.5 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded font-medium text-sm hover:opacity-90 disabled:opacity-40 flex items-center justify-center gap-1.5"
          >
            {addCpi.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Plus size={14} />}
            Add / Update
          </button>
        </form>
        {addCpi.error && <ErrorBanner message={addCpi.error.message} />}
      </section>

      {/* ── Rate Series Manager ──────────────────────────────────────────── */}
      <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Percent size={20} />
            <h2 className="text-lg font-semibold">Rate Series — Lãi suất (Manual Entry)</h2>
          </div>
          <a
            href="https://sbv.gov.vn/webcenter/portal/en/home/rm/ir"
            target="_blank"
            rel="noreferrer"
            className="text-xs text-slate-500 hover:text-slate-900 dark:hover:text-slate-100 inline-flex items-center gap-1"
          >
            SBV policy rates <ExternalLink size={12} />
          </a>
        </div>
        <p className="text-xs text-slate-500">
          SBV refinance rate (primary policy lever) + optional interbank ON (system liquidity) + 12M deposit rate (funding cost proxy). All in percent.
        </p>

        {rateRows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b border-slate-200 dark:border-slate-800 text-slate-500">
                  <th className="py-2 px-2 font-medium">Month</th>
                  <th className="py-2 px-2 font-medium text-right">Refinance %</th>
                  <th className="py-2 px-2 font-medium text-right">Interbank ON %</th>
                  <th className="py-2 px-2 font-medium text-right">Deposit 12M %</th>
                  <th className="py-2 px-2 font-medium">Source</th>
                  <th className="py-2 px-2"></th>
                </tr>
              </thead>
              <tbody>
                {rateRows.slice(-8).map((r) => (
                  <tr key={r.date} className="border-b border-slate-100 dark:border-slate-800/60">
                    <td className="py-2 px-2 font-mono">{r.date}</td>
                    <td className="py-2 px-2 text-right font-mono">{r.refinance.toFixed(2)}</td>
                    <td className="py-2 px-2 text-right font-mono text-slate-500">
                      {r.interbank_on != null ? r.interbank_on.toFixed(2) : "—"}
                    </td>
                    <td className="py-2 px-2 text-right font-mono text-slate-500">
                      {r.deposit_12m != null ? r.deposit_12m.toFixed(2) : "—"}
                    </td>
                    <td className="py-2 px-2 text-slate-500 text-xs">{r.source || "—"}</td>
                    <td className="py-2 px-2 text-right">
                      <button
                        onClick={() => removeRate.mutate(r.date)}
                        disabled={removeRate.isPending}
                        className="p-1 rounded hover:bg-red-50 dark:hover:bg-red-950/40 text-red-600 disabled:opacity-40"
                      >
                        <X size={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rateRows.length > 8 && (
              <p className="text-xs text-slate-500 mt-2">Showing last 8 of {rateRows.length} observations.</p>
            )}
          </div>
        )}

        <form
          className="grid grid-cols-2 md:grid-cols-6 gap-3 items-end pt-2 border-t border-slate-200 dark:border-slate-800"
          onSubmit={(e) => {
            e.preventDefault();
            if (canAddRate) addRate.mutate();
          }}
        >
          <div>
            <label className="block text-xs text-slate-500 mb-1">Month</label>
            <input
              type="month"
              value={rateDate}
              onChange={(e) => setRateDate(e.target.value)}
              className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
              required
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Refinance %</label>
            <input
              type="number" step="0.01"
              value={rateRefi}
              onChange={(e) => setRateRefi(e.target.value)}
              placeholder="4.50"
              className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
              required
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Interbank ON %</label>
            <input
              type="number" step="0.01"
              value={rateInterbank}
              onChange={(e) => setRateInterbank(e.target.value)}
              placeholder="3.20"
              className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Deposit 12M %</label>
            <input
              type="number" step="0.01"
              value={rateDeposit}
              onChange={(e) => setRateDeposit(e.target.value)}
              placeholder="5.40"
              className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Source</label>
            <input
              value={rateSource}
              onChange={(e) => setRateSource(e.target.value)}
              className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
            />
          </div>
          <button
            type="submit"
            disabled={!canAddRate || addRate.isPending}
            className="px-3 py-1.5 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded font-medium text-sm hover:opacity-90 disabled:opacity-40 flex items-center justify-center gap-1.5"
          >
            {addRate.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Plus size={14} />}
            Add / Update
          </button>
        </form>
        {addRate.error && <ErrorBanner message={addRate.error.message} />}
      </section>

      {pillars.data && (
        <ResultPanel>
          <MarkdownBlock text={pillars.data.text} />
        </ResultPanel>
      )}
      {pillars.error && <ErrorBanner message={pillars.error.message} />}

      {cycle.data && (
        <ResultPanel>
          <MarkdownBlock text={cycle.data.text} />
        </ResultPanel>
      )}
      {cycle.error && <ErrorBanner message={cycle.error.message} />}

      {rotation.data && (
        <ResultPanel>
          <MarkdownBlock text={rotation.data.text} />
        </ResultPanel>
      )}
      {rotation.error && <ErrorBanner message={rotation.error.message} />}

      {money.data && (
        <ResultPanel>
          <MarkdownBlock text={money.data.text} />
        </ResultPanel>
      )}
      {money.error && <ErrorBanner message={money.error.message} />}

      {/* ── Macro Report Reader ─────────────────────────────────────────── */}
      <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm space-y-4">
        <div className="flex items-center gap-2">
          <FileText size={20} />
          <h2 className="text-lg font-semibold">Load Macro Report</h2>
        </div>
        <p className="text-xs text-slate-500">
          Read a broker macro PDF (SSI Research, VCBS, Mirae, BVSC…) or SBV/GSO policy paper.
          Extracts text, surfaces GDP/CPI/M2/rate mentions, optionally saves to knowledge base.
          For scanned PDFs, use <code>load_financial_pdf</code> for visual reading instead.
        </p>

        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (canLoad) loadReport.mutate();
          }}
        >
          <div className="grid md:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-500 mb-1">PDF URL</label>
              <input
                value={reportUrl}
                onChange={(e) => setReportUrl(e.target.value)}
                placeholder="https://…/macro-monthly.pdf"
                disabled={reportFile !== null}
                className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm disabled:opacity-40"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Or upload file</label>
              <input
                type="file"
                accept="application/pdf"
                onChange={(e) => setReportFile(e.target.files?.[0] ?? null)}
                className="w-full text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Broker</label>
              <input
                value={reportBroker}
                onChange={(e) => setReportBroker(e.target.value)}
                placeholder="SSI Research"
                className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Title (optional)</label>
              <input
                value={reportTitle}
                onChange={(e) => setReportTitle(e.target.value)}
                placeholder="Vietnam Macro Monthly — June 2026"
                className="w-full px-2 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 text-sm"
              />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={reportSave}
                onChange={(e) => setReportSave(e.target.checked)}
              />
              Save to knowledge base (`knowledge/sources/macro/`)
            </label>
            <button
              type="submit"
              disabled={!canLoad || loadReport.isPending}
              className="ml-auto px-4 py-1.5 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded font-medium text-sm hover:opacity-90 disabled:opacity-40 flex items-center gap-1.5"
            >
              {loadReport.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Upload size={14} />}
              {loadReport.isPending ? "Reading..." : "Load Report"}
            </button>
          </div>
        </form>
      </section>

      {loadReport.data && (
        <ResultPanel>
          <MarkdownBlock text={loadReport.data.text} />
        </ResultPanel>
      )}
      {loadReport.error && <ErrorBanner message={loadReport.error.message} />}

      {/* ── Broker Feed ────────────────────────────────────────────────── */}
      <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Cloud size={20} />
            <h2 className="text-lg font-semibold">Fetch from Broker</h2>
          </div>
          <button
            onClick={() => brokerFeed.refetch()}
            disabled={brokerFeed.isFetching}
            className="px-3 py-1.5 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded font-medium text-sm hover:opacity-90 disabled:opacity-40 flex items-center gap-1.5"
          >
            {brokerFeed.isFetching ? <RefreshCw size={14} className="animate-spin" /> : <Download size={14} />}
            {brokerFeed.isFetching ? "Fetching..." : "Fetch Latest (MASVN)"}
          </button>
        </div>
        <p className="text-xs text-slate-500">
          Pulls the latest macro/strategy reports directly from Mirae Asset Securities Vietnam.
          One-click "Load &amp; Save" ingests them into the knowledge base.
        </p>

        {brokerFeed.error && <ErrorBanner message={brokerFeed.error.message} />}

        {brokerFeed.data && brokerFeed.data.reports.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b border-slate-200 dark:border-slate-800 text-slate-500">
                  <th className="py-2 px-2 font-medium">Date</th>
                  <th className="py-2 px-2 font-medium">Title</th>
                  <th className="py-2 px-2 font-medium text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {brokerFeed.data.reports.map((r) => {
                  const isLoading = loadFromFeed.isPending && loadFromFeed.variables?.pdf_url === r.pdf_url;
                  return (
                    <tr key={r.id} className="border-b border-slate-100 dark:border-slate-800/60">
                      <td className="py-2 px-2 text-slate-500 whitespace-nowrap">{r.date}</td>
                      <td className="py-2 px-2 text-slate-800 dark:text-slate-200">
                        <a href={r.page_url} target="_blank" rel="noreferrer" className="hover:underline">
                          {r.title_vi || r.title_en || "(untitled)"}
                        </a>
                      </td>
                      <td className="py-2 px-2 text-right">
                        {r.pdf_url ? (
                          <button
                            onClick={() => loadFromFeed.mutate({ pdf_url: r.pdf_url, title_vi: r.title_vi })}
                            disabled={loadFromFeed.isPending}
                            className="px-3 py-1 bg-slate-700 dark:bg-slate-300 text-white dark:text-slate-900 rounded text-xs font-medium hover:opacity-90 disabled:opacity-40 inline-flex items-center gap-1.5"
                          >
                            {isLoading ? <RefreshCw size={12} className="animate-spin" /> : <Download size={12} />}
                            {isLoading ? "Loading..." : "Load & Save"}
                          </button>
                        ) : (
                          <span className="text-xs text-slate-400">no PDF</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="text-xs text-slate-500 mt-2">
              Total available: {brokerFeed.data.total} · Showing {brokerFeed.data.reports.length}
            </p>
          </div>
        )}

        {loadFromFeed.data && (
          <div className="pt-3 border-t border-slate-200 dark:border-slate-800">
            <MarkdownBlock text={loadFromFeed.data.text} />
          </div>
        )}
        {loadFromFeed.error && <ErrorBanner message={loadFromFeed.error.message} />}
      </section>

      {/* ── Saved Reports Library ────────────────────────────────────────── */}
      {reportsList.data && (
        <ResultPanel>
          <MarkdownBlock text={reportsList.data.text} />
        </ResultPanel>
      )}
    </div>
  );
}

function AnalysisCard({
  icon, title, hint, onRun, pending, extra,
}: {
  icon: React.ReactNode;
  title: string;
  hint: string;
  onRun: () => void;
  pending: boolean;
  extra?: React.ReactNode;
}) {
  return (
    <div className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-4 shadow-sm">
      <div className="flex items-center gap-2 mb-1">
        {icon}
        <h3 className="font-semibold">{title}</h3>
      </div>
      <p className="text-xs text-slate-500 mb-3">{hint}</p>
      <button
        onClick={onRun}
        disabled={pending}
        className="w-full px-3 py-1.5 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded font-medium text-sm hover:opacity-90 disabled:opacity-40 flex items-center justify-center gap-1.5"
      >
        {pending ? <RefreshCw size={14} className="animate-spin" /> : icon}
        {pending ? "Running..." : "Run"}
      </button>
      {extra}
    </div>
  );
}

function ResultPanel({ children }: { children: React.ReactNode }) {
  return (
    <section className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-6 shadow-sm">
      {children}
    </section>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="rounded-md bg-red-50 dark:bg-red-950/50 border border-red-300 dark:border-red-800 p-4 flex items-start gap-2 text-red-700 dark:text-red-400 text-sm">
      <AlertCircle size={16} className="mt-0.5 shrink-0" />
      <span>{message}</span>
    </div>
  );
}
