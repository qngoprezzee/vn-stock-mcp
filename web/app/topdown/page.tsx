"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  moneySupply, macroIndicators,
  marketCycle,
  sectorRotation, topTickersBySector,
  stockQualityScore, stockEarningsQuality, stockDCF,
  stockTechnical, stockMoneyFlowPriceAction, stockForeignFlow, fetchBrokerNews,
} from "@/lib/api";
import { MarkdownBlock } from "@/components/MarkdownBlock";
import {
  RefreshCw, AlertCircle, CheckCircle2, ArrowDown,
  Globe, Compass, Layers, Search, Calculator, BookOpen, Sparkles,
} from "lucide-react";

type TierState = "locked" | "ready" | "running" | "done";

const TIER_META = [
  { id: 1, title: "Macro Conditions", icon: Globe,     hint: "Money supply, credit conditions, currency" },
  { id: 2, title: "Market Cycle",     icon: Compass,   hint: "8-phase classification, positioning rule" },
  { id: 3, title: "Sector Rotation",  icon: Layers,    hint: "Leading sectors, cyclical vs defensive" },
  { id: 4, title: "Stock Selection",  icon: Search,    hint: "Deep dive on tickers within leading sectors" },
  { id: 5, title: "Position Sizing",  icon: Calculator, hint: "ATR-based shares, stops, R:R (via /portfolio, /position-sizer)" },
  { id: 6, title: "Journal",          icon: BookOpen,  hint: "Write thesis + decision log BEFORE executing" },
] as const;

export default function TopdownPage() {
  const [tier, setTier] = useState<number>(1);
  const [ticker, setTicker] = useState("FPT");

  // Tier 1 mutations
  const money = useMutation({ mutationFn: moneySupply });
  const macro = useMutation({ mutationFn: macroIndicators });

  // Tier 2
  const cycle = useMutation({ mutationFn: marketCycle });

  // Tier 3
  const rotation = useMutation({ mutationFn: () => sectorRotation("3M") });
  const topPicks = useMutation({ mutationFn: () => topTickersBySector("3M", 3, 5) });

  // Tier 4 — stock deep dive on selected ticker
  const quality = useMutation({ mutationFn: (t: string) => stockQualityScore(t) });
  const earnings = useMutation({ mutationFn: (t: string) => stockEarningsQuality(t) });
  const dcf = useMutation({ mutationFn: (t: string) => stockDCF({ ticker: t }) });
  const technical = useMutation({ mutationFn: (t: string) => stockTechnical(t) });
  const flow = useMutation({ mutationFn: (t: string) => stockMoneyFlowPriceAction({ ticker: t }) });
  const foreign = useMutation({ mutationFn: (t: string) => stockForeignFlow(t) });
  const news = useMutation({ mutationFn: (t: string) => fetchBrokerNews({ ticker: t, limit: 10 }) });

  const runTier1 = () => {
    money.mutate();
    macro.mutate();
  };

  const runTier4 = () => {
    const t = ticker.trim().toUpperCase();
    if (!t) return;
    quality.mutate(t);
    earnings.mutate(t);
    dcf.mutate(t);
    technical.mutate(t);
    flow.mutate(t);
    foreign.mutate(t);
    news.mutate(t);
  };

  const tier1Done = money.data && macro.data;
  const tier2Done = !!cycle.data;
  const tier3Done = !!rotation.data;
  const tier4Done = quality.data && earnings.data && dcf.data && technical.data && flow.data && foreign.data && news.data;

  const advance = (next: number) => setTier(Math.max(tier, next));

  const stateOf = (tierId: number): TierState => {
    if (tier < tierId) return "locked";
    if (tierId === 1 && (money.isPending || macro.isPending)) return "running";
    if (tierId === 1 && tier1Done) return "done";
    if (tierId === 2 && cycle.isPending) return "running";
    if (tierId === 2 && tier2Done) return "done";
    if (tierId === 3 && rotation.isPending) return "running";
    if (tierId === 3 && tier3Done) return "done";
    if (tierId === 4 && (quality.isPending || earnings.isPending || dcf.isPending || technical.isPending || flow.isPending || foreign.isPending || news.isPending)) return "running";
    if (tierId === 4 && tier4Done) return "done";
    return "ready";
  };

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
          <ArrowDown size={28} />
          Top-Down Analysis
        </h1>
        <p className="text-slate-600 dark:text-slate-400 mt-1">
          Phân tích chứng khoán top-down — Macro → Market → Sector → Stock → Position → Journal.
          Each tier must pass before the next unlocks. Skip tiers = skipping the reason a stock actually moves.
        </p>
      </header>

      <TimelineTracker states={TIER_META.map((t) => stateOf(t.id))} activeTier={tier} />

      {/* Tier 1 — Macro */}
      <TierBlock
        tierId={1} title="Macro Conditions" icon={Globe} hint={TIER_META[0].hint}
        state={stateOf(1)}
      >
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Are monetary conditions supportive? Loose credit + stable VND + inflation in-target = green light.
          Tight + FX pressure = cash preferred, stop here.
        </p>
        <ActionButton onClick={runTier1} pending={money.isPending || macro.isPending} label="Run Tier 1" />
        {money.data && (
          <ResultPanel title="Money Supply"><MarkdownBlock text={money.data.text} /></ResultPanel>
        )}
        {macro.data && (
          <ResultPanel title="Macro Indicators"><MarkdownBlock text={macro.data.text} /></ResultPanel>
        )}
        {tier1Done && (
          <NextButton onClick={() => advance(2)} disabled={tier >= 2} />
        )}
      </TierBlock>

      {/* Tier 2 — Market Cycle */}
      <TierBlock
        tierId={2} title="Market Cycle" icon={Compass} hint={TIER_META[1].hint}
        state={stateOf(2)}
      >
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Combines Tier 1 credit signal with VN-Index MA200 trend and sector leadership.
          Returns one of 8 phases (Bottom / Early Recovery / Mid Expansion / Late Cycle / Distribution / Bear / etc.).
        </p>
        <ActionButton onClick={() => cycle.mutate()} pending={cycle.isPending} label="Run Tier 2" />
        {cycle.data && (
          <ResultPanel title="Cycle Phase"><MarkdownBlock text={cycle.data.text} /></ResultPanel>
        )}
        {tier2Done && (
          <NextButton onClick={() => advance(3)} disabled={tier >= 3} />
        )}
      </TierBlock>

      {/* Tier 3 — Sector */}
      <TierBlock
        tierId={3} title="Sector Rotation" icon={Layers} hint={TIER_META[2].hint}
        state={stateOf(3)}
      >
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Which sectors are leading vs VN-Index? Cyclical leadership confirms bull trend; defensive
          leadership warns of risk-off. Pick top 2-3 sectors that match the cycle from Tier 2.
        </p>
        <ActionButton
          onClick={() => {
            rotation.mutate();
            topPicks.mutate();
          }}
          pending={rotation.isPending || topPicks.isPending}
          label="Run Tier 3"
        />
        {rotation.data && (
          <ResultPanel title="Sector Rotation"><MarkdownBlock text={rotation.data.text} /></ResultPanel>
        )}

        {topPicks.data && !topPicks.data.error && (
          <div className="border border-emerald-300 dark:border-emerald-700/60 bg-emerald-50/40 dark:bg-emerald-950/20 rounded-md p-4 space-y-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-emerald-800 dark:text-emerald-200">
              <Sparkles size={16} />
              Top Tickers to Deep-Dive in Tier 4 (by {topPicks.data.rank_by})
            </div>
            <p className="text-xs text-emerald-800/80 dark:text-emerald-200/80">
              Click a ticker to autofill it in Tier 4 Stock Selection below. Green = alpha vs VN-Index.
            </p>
            <div className="space-y-2">
              {topPicks.data.sectors.map((sec) => (
                <div key={sec.name}>
                  <div className="text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                    <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] mr-1.5 ${
                      sec.type === "cyclical" ? "bg-emerald-200 dark:bg-emerald-800/60 text-emerald-800 dark:text-emerald-200"
                      : sec.type === "defensive" ? "bg-slate-200 dark:bg-slate-700 text-slate-700 dark:text-slate-300"
                      : "bg-amber-200 dark:bg-amber-800/60 text-amber-800 dark:text-amber-200"
                    }`}>
                      {sec.type}
                    </span>
                    <strong>{sec.name}</strong>
                    <span className="text-slate-500 ml-2">
                      3M {sec.sector_returns["3M"]?.toFixed(1) ?? "—"}%
                      {sec.alpha_vs_vni != null && (
                        <span className={sec.alpha_vs_vni > 0 ? " text-emerald-600" : " text-red-600"}>
                          {" "}({sec.alpha_vs_vni > 0 ? "+" : ""}{sec.alpha_vs_vni.toFixed(1)}pp)
                        </span>
                      )}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {sec.tickers.map((t) => {
                      const isPositive = (t.alpha_vs_vni ?? 0) > 0;
                      return (
                        <button
                          key={t.ticker}
                          onClick={() => {
                            setTicker(t.ticker);
                            advance(4);
                          }}
                          className={`px-2 py-1 rounded text-xs font-mono font-semibold border transition-colors ${
                            isPositive
                              ? "border-emerald-400 bg-white dark:bg-slate-900 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-100 dark:hover:bg-emerald-900/40"
                              : "border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800"
                          }`}
                          title={`${t.ticker} · 3M ${t.returns["3M"]?.toFixed(1) ?? "—"}% · alpha ${t.alpha_vs_vni?.toFixed(1) ?? "—"}pp`}
                        >
                          {t.ticker}
                          <span className={`ml-1 text-[10px] font-normal ${isPositive ? "text-emerald-600" : "text-slate-500"}`}>
                            {t.returns["3M"] != null ? `${t.returns["3M"] > 0 ? "+" : ""}${t.returns["3M"].toFixed(1)}%` : "—"}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {tier3Done && (
          <NextButton onClick={() => advance(4)} disabled={tier >= 4} />
        )}
      </TierBlock>

      {/* Tier 4 — Stock */}
      <TierBlock
        tierId={4} title="Stock Selection" icon={Search} hint={TIER_META[3].hint}
        state={stateOf(4)}
      >
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Pick a ticker from the leading sectors. Runs 7 analyses in parallel: quality, earnings quality,
          DCF valuation, technical, money flow (with Wyckoff), foreign flow, broker news.
        </p>
        <div className="flex items-center gap-3">
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            placeholder="FPT"
            className="w-32 px-3 py-1.5 border border-slate-300 dark:border-slate-700 rounded bg-white dark:bg-slate-800 uppercase font-mono text-sm"
          />
          <ActionButton onClick={runTier4} pending={quality.isPending || earnings.isPending || dcf.isPending || technical.isPending || flow.isPending || foreign.isPending || news.isPending} label="Deep-Dive Ticker" />
        </div>
        {quality.data   && <ResultPanel title={`${ticker} — Quality Score`}><MarkdownBlock text={quality.data.text} /></ResultPanel>}
        {earnings.data  && <ResultPanel title={`${ticker} — Earnings Quality`}><MarkdownBlock text={earnings.data.text} /></ResultPanel>}
        {dcf.data       && <ResultPanel title={`${ticker} — DCF Valuation`}><MarkdownBlock text={dcf.data.text} /></ResultPanel>}
        {technical.data && <ResultPanel title={`${ticker} — Technical`}><MarkdownBlock text={technical.data.text} /></ResultPanel>}
        {flow.data      && <ResultPanel title={`${ticker} — Money Flow / Wyckoff`}><MarkdownBlock text={flow.data.text} /></ResultPanel>}
        {foreign.data   && <ResultPanel title={`${ticker} — Foreign Flow`}><MarkdownBlock text={foreign.data.text} /></ResultPanel>}
        {news.data      && <ResultPanel title={`${ticker} — Broker News`}><MarkdownBlock text={news.data.text} /></ResultPanel>}
        {tier4Done && (
          <NextButton onClick={() => advance(5)} disabled={tier >= 5} />
        )}
      </TierBlock>

      {/* Tier 5 — Position */}
      <TierBlock
        tierId={5} title="Position Sizing" icon={Calculator} hint={TIER_META[4].hint}
        state={stateOf(5)}
      >
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Compute shares to buy, stop-loss, R:R. This lives on <a href="/position-sizer" className="underline text-slate-900 dark:text-slate-100">/position-sizer</a>.
          Also stress-test the addition against your existing portfolio at <a href="/portfolio" className="underline text-slate-900 dark:text-slate-100">/portfolio</a>.
        </p>
        <div className="flex gap-3">
          <a href="/position-sizer" className="px-4 py-1.5 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded font-medium text-sm inline-flex items-center gap-1.5">
            <Calculator size={14} /> Open Position Sizer
          </a>
          <a href="/portfolio" className="px-4 py-1.5 border border-slate-300 dark:border-slate-700 rounded font-medium text-sm inline-flex items-center gap-1.5">
            Open Portfolio Risk
          </a>
        </div>
        <div className="pt-1">
          <button
            onClick={() => advance(6)}
            disabled={tier >= 6}
            className="text-sm text-slate-500 hover:text-slate-900 dark:hover:text-slate-100 disabled:opacity-40 underline"
          >
            I have sized the position — continue to journal →
          </button>
        </div>
      </TierBlock>

      {/* Tier 6 — Journal */}
      <TierBlock
        tierId={6} title="Journal" icon={BookOpen} hint={TIER_META[5].hint}
        state={stateOf(6)}
      >
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Write your thesis <strong>before</strong> executing. A thesis written after entry is rationalization.
          Save the trade decision on execution. Review monthly.
        </p>
        <div className="flex gap-3 flex-wrap">
          <a href="/thesis" className="px-4 py-1.5 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded font-medium text-sm inline-flex items-center gap-1.5">
            <BookOpen size={14} /> Write Investment Thesis
          </a>
          <a href="/performance" className="px-4 py-1.5 border border-slate-300 dark:border-slate-700 rounded font-medium text-sm inline-flex items-center gap-1.5">
            Review Performance
          </a>
        </div>
      </TierBlock>
    </div>
  );
}

// ─── Components ─────────────────────────────────────────────────────────────

function TimelineTracker({ states, activeTier }: { states: TierState[]; activeTier: number }) {
  return (
    <div className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 p-4 shadow-sm">
      <div className="flex items-center justify-between">
        {TIER_META.map((t, i) => {
          const state = states[i];
          const active = t.id === activeTier;
          const done = state === "done";
          return (
            <div key={t.id} className="flex-1 flex flex-col items-center">
              <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-semibold ${
                done ? "bg-emerald-600 text-white" :
                active ? "bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900" :
                state === "locked" ? "bg-slate-200 dark:bg-slate-800 text-slate-400" :
                "bg-slate-300 dark:bg-slate-700 text-slate-600 dark:text-slate-300"
              }`}>
                {done ? <CheckCircle2 size={16} /> : t.id}
              </div>
              <span className="mt-1.5 text-[10px] text-slate-500 hidden sm:block">{t.title}</span>
              {i < TIER_META.length - 1 && (
                <div className="hidden sm:block w-full h-0.5 -mt-4 -mr-8 relative -z-10">
                  <div className={`h-full ${done ? "bg-emerald-600" : "bg-slate-200 dark:bg-slate-800"}`} style={{ marginLeft: "50%", marginRight: "-50%" }} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TierBlock({
  tierId, title, icon: Icon, hint, state, children,
}: {
  tierId: number;
  title: string;
  icon: React.ComponentType<{ size?: number }>;
  hint: string;
  state: TierState;
  children: React.ReactNode;
}) {
  const locked = state === "locked";
  return (
    <section className={`bg-white dark:bg-slate-900 rounded-lg border p-6 shadow-sm space-y-4 ${
      locked
        ? "border-slate-200 dark:border-slate-800 opacity-50"
        : state === "done"
        ? "border-emerald-300 dark:border-emerald-700/60"
        : "border-slate-300 dark:border-slate-700"
    }`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-full bg-slate-100 dark:bg-slate-800 text-xs font-bold flex items-center justify-center">
            {tierId}
          </div>
          <Icon size={20} />
          <h2 className="text-lg font-semibold">{title}</h2>
          {state === "done" && <CheckCircle2 size={18} className="text-emerald-600" />}
        </div>
        <span className="text-xs text-slate-500">{hint}</span>
      </div>
      {!locked && children}
      {locked && (
        <p className="text-sm text-slate-500 italic">Complete the previous tier to unlock.</p>
      )}
    </section>
  );
}

function ActionButton({ onClick, pending, label }: { onClick: () => void; pending: boolean; label: string }) {
  return (
    <button
      onClick={onClick}
      disabled={pending}
      className="px-4 py-1.5 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 rounded font-medium text-sm hover:opacity-90 disabled:opacity-40 flex items-center gap-1.5"
    >
      {pending ? <RefreshCw size={14} className="animate-spin" /> : null}
      {pending ? "Running..." : label}
    </button>
  );
}

function NextButton({ onClick, disabled }: { onClick: () => void; disabled: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="text-sm text-slate-500 hover:text-slate-900 dark:hover:text-slate-100 disabled:opacity-40 underline inline-flex items-center gap-1"
    >
      Continue to next tier <ArrowDown size={12} />
    </button>
  );
}

function ResultPanel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <details className="border border-slate-200 dark:border-slate-800 rounded-md" open>
      <summary className="cursor-pointer px-3 py-2 text-sm font-semibold bg-slate-50 dark:bg-slate-800/50 hover:bg-slate-100 dark:hover:bg-slate-800">
        {title}
      </summary>
      <div className="p-4">{children}</div>
    </details>
  );
}
