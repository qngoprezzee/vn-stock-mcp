/**
 * Typed client for the VN Stock MCP HTTP API (FastAPI, default localhost:8000).
 * Each function maps 1:1 to an endpoint in api.py.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export type TextResponse = { text: string };

export type Holding = {
  ticker: string;
  shares: number;
  avg_cost: number;
};

export type Conviction = "low" | "medium" | "high";

export type DecisionRow = {
  date: string;
  ticker: string;
  action: string;
  price: number;
  quantity: number;
  rationale: string;
  outcome: string;
};

export type ClosedTrade = {
  ticker: string;
  buy_date: string;
  sell_date: string;
  buy_price: number;
  sell_price: number;
  qty: number;
  pnl: number;
  pnl_pct: number;
  hold_days: number;
};

export type PerformanceMetrics = {
  total_trades?: number;
  winners?: number;
  losers?: number;
  win_rate?: number;
  avg_winner_pct?: number;
  avg_loser_pct?: number;
  expectancy_pct?: number;
  total_pnl?: number;
  max_consecutive_losses?: number;
  avg_hold_days?: number;
};

export type DecisionsRawResponse = {
  decisions: DecisionRow[];
  closed_trades: ClosedTrade[];
  open_positions: Record<string, { qty: number; avg_cost: number; first_buy: string }>;
  metrics: PerformanceMetrics;
  clusters: {
    by_ticker: Record<string, number>;
    by_hold_period: Record<string, number>;
    patterns: string[];
  };
};

async function postJSON<T>(path: string, body: object): Promise<T> {
  const resp = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`API ${resp.status}: ${detail.slice(0, 200)}`);
  }
  return resp.json();
}

async function getJSON<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_URL}${path}`);
  if (!resp.ok) {
    throw new Error(`API ${resp.status}`);
  }
  return resp.json();
}

// ── Stock ────────────────────────────────────────────────────────────────────

export const stockOverview = (ticker: string) =>
  postJSON<TextResponse>("/api/stock/overview", { ticker });

export const stockQualityScore = (ticker: string) =>
  postJSON<TextResponse>("/api/stock/quality-score", { ticker });

export const stockEarningsQuality = (ticker: string) =>
  postJSON<TextResponse>("/api/stock/earnings-quality", { ticker });

export const stockForeignFlow = (ticker: string) =>
  postJSON<TextResponse>("/api/stock/foreign-flow", { ticker });

export const stockTechnical = (ticker: string) =>
  postJSON<TextResponse>("/api/stock/technical", { ticker });

export const stockNewsCorrelation = (params: { ticker: string; lookback_days?: number }) =>
  postJSON<TextResponse>("/api/stock/news-correlation", params);

export const stockDCF = (params: {
  ticker: string;
  discount_rate?: number;
  terminal_growth?: number;
  bull_growth?: number;
  base_growth?: number;
  bear_growth?: number;
  projection_years?: number;
}) => postJSON<TextResponse>("/api/stock/dcf", params);

export const compareStocks = (tickers: string[], period: "year" | "quarter" = "year") =>
  postJSON<TextResponse>("/api/compare", { tickers, period });

// ── Market ───────────────────────────────────────────────────────────────────

export const marketOverview = () => getJSON<TextResponse>("/api/market/overview");
export const economyNews = (limit = 20) =>
  getJSON<TextResponse>(`/api/market/economy-news?limit=${limit}`);
export const macroData = () => getJSON<TextResponse>("/api/market/macro-data");
export const macroIndicators = () => getJSON<TextResponse>("/api/market/macro-indicators");
export const commodities = () => getJSON<TextResponse>("/api/market/commodities");

// ── Risk & portfolio ─────────────────────────────────────────────────────────

export const positionSizing = (params: {
  ticker: string;
  portfolio_value: number;
  risk_per_trade_pct?: number;
  conviction?: Conviction;
  atr_multiplier?: number;
}) => postJSON<TextResponse>("/api/risk/position-sizing", params);

export const stressTest = (holdings: Holding[]) =>
  postJSON<TextResponse>("/api/risk/stress-test", { holdings });

// ── Watchlist ────────────────────────────────────────────────────────────────

export const watchlistManage = (action: "add" | "remove" | "list" | "clear", ticker = "") =>
  postJSON<TextResponse>("/api/watchlist/manage", { action, ticker });

export const watchlistCheck = () => getJSON<TextResponse>("/api/watchlist/check");

export const watchlistRaw = () => getJSON<{ tickers: string[] }>("/api/watchlist/raw");

// ── Journal & review ─────────────────────────────────────────────────────────

export const saveThesis = (params: {
  ticker: string;
  thesis: string;
  buy_price: number;
  target_price: number;
  stop_price: number;
  falsification_criteria: string;
  conviction?: string;
  catalysts?: string;
  strongest_bias?: string;
  premortem_reason?: string;
}) => postJSON<TextResponse>("/api/journal/thesis", params);

export const saveDecision = (params: {
  ticker: string;
  action: "BUY" | "SELL" | "ADD" | "TRIM" | "HOLD";
  price: number;
  rationale: string;
  quantity?: number;
  outcome?: string;
}) => postJSON<TextResponse>("/api/journal/decision", params);

export const reviewPerformance = (lookback_days = 365) =>
  postJSON<TextResponse>("/api/journal/review", { lookback_days });

export const decisionsRaw = () => getJSON<DecisionsRawResponse>("/api/journal/decisions-raw");

export const apiHealth = () => getJSON<{ status: string }>("/health");

// ── Knowledge layer (K6-K9) ────────────────────────────────────────────────

export const knowledgeThesisContext = (params: {
  ticker: string;
  lookback_days?: number;
  max_articles?: number;
  include_sector_principles?: boolean;
}) => postJSON<TextResponse>("/api/knowledge/thesis-context", params);

export const knowledgeCompareAuthors = (params: {
  topic: string;
  authors: string[];
  keywords?: string[];
  context_paragraphs?: number;
  max_per_author?: number;
}) => postJSON<TextResponse>("/api/knowledge/compare-authors", params);

export type BriefStatus = "synthesized" | "pending" | "missing";

export const knowledgeBriefRead = (date: string) =>
  getJSON<{ status: BriefStatus; path: string; content: string }>(
    `/api/knowledge/daily-brief/${date}`,
  );

export const knowledgeBriefGather = (date = "") =>
  postJSON<{ pending_path: string; date: string; content: string }>(
    "/api/knowledge/daily-brief/gather",
    { date },
  );

export const knowledgeCorpusStats = () =>
  getJSON<{
    total: number;
    last_run: string | null;
    by_category: Record<string, number>;
    top_sources: Record<string, number>;
  }>("/api/knowledge/corpus-stats");

export type GlossaryConcept = {
  name: string;
  category: string;
  definition: string;
  formula?: string;
  key_quote?: {
    text: string;
    author: string;
    source_id: string | null;
    context: string;
  };
  when_to_use?: string;
  common_pitfalls?: string[];
};

export const knowledgeGlossary = () =>
  getJSON<{
    version: number;
    concepts: Record<string, GlossaryConcept>;
  }>("/api/knowledge/glossary");
