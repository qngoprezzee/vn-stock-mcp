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

export type IndexInfo = { value: number; change: number; change_pct: number; volume: number };
export type Mover     = { ticker: string; value: number; change: number; change_pct: number; volume: number };
export type DashboardData = {
  indices: { vnindex: IndexInfo; hnx: IndexInfo; upcom: IndexInfo };
  gainers: Mover[];
  losers:  Mover[];
  universe_size: number;
  movers_age_s:  number | null;
};
export type IndexPrice = { date: string; value: number; volume: number };

export const marketDashboardData = () => getJSON<DashboardData>("/api/market/dashboard-data");
export const marketIndexChart = (index = "VNINDEX", days = 365) =>
  getJSON<{ index: string; prices: IndexPrice[] }>(`/api/market/index-chart?index=${index}&days=${days}`);

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

export const apiHealth = () => getJSON<{ status: string }>("/health")

// ── Chart data ───────────────────────────────────────────────────────────────

export type PriceBar = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type OverviewData = {
  ticker: string;
  name: string;
  sector: string;
  price: number;
  change_pct: number;
  market_cap_t: number;
  high_52w: number;
  low_52w: number;
  rating: string;
  target_price: number;
  foreign_pct: number;
};

export type IncomeTrend = {
  ticker: string;
  years: string[];
  revenue: (number | null)[];
  net_income: (number | null)[];
  gross_profit: (number | null)[];
};

export type Indicators = {
  ma20: boolean;
  ma50: boolean;
  ma200: boolean;
  bb: boolean;
  rsi: boolean;
  macd: boolean;
  volume: boolean;
};

export type TechnicalData = {
  ticker: string;
  n_days: number;
  verdict: "BULLISH" | "MILD_BULLISH" | "NEUTRAL" | "MILD_BEARISH" | "BEARISH";
  score: number;
  max_score: number;
  price: number;
  mas: { ma20: number | null; ma50: number | null; ma200: number | null; pct_from_ma20: number | null; pct_from_ma50: number | null; pct_from_ma200: number | null };
  rsi: number | null;
  macd: { macd: number | null; signal: number | null; hist: number | null };
  bb: { upper: number | null; mid: number | null; lower: number | null; pct_b: number | null };
  atr: number | null;
  atr_pct: number | null;
  volume: { last: number; avg20: number; ratio: number };
  levels: { resistance: number; pivot: number; support: number; w52_high: number; w52_low: number; pct_from_high: number; pct_from_low: number };
};

export const stockTechnicalData = (ticker: string) =>
  getJSON<TechnicalData>(`/api/stock/technical-data?ticker=${ticker}`);

export type CheckItem = { title: string; description: string; isPass: boolean };
export type ScorePoint = { point: number };
export type ExecutiveSummary = {
  ticker: string;
  rewards: CheckItem[];
  risks: CheckItem[];
  valuationPoint:       ScorePoint;
  growthPoint:          ScorePoint;
  passPerformancePoint: ScorePoint;
  financialHealthPoint: ScorePoint;
  dividendPoint:        ScorePoint;
  companyQuality: number;
  overallRiskLevel: string;
  qualityValuation: string;
  downsideRisk: number;
  sharpeRatio: number;
  downsideRiskLevel: string;
  liquidityRiskLevel: string;
  liquidityMsg: string;
  taSignal1d: string;
};

export const stockExecutiveSummary = (ticker: string) =>
  getJSON<ExecutiveSummary>(`/api/stock/executive-summary?ticker=${ticker}`);

export const stockChartData = (ticker: string, days = 90) =>
  getJSON<{ ticker: string; prices: PriceBar[] }>(
    `/api/stock/chart-data?ticker=${ticker}&days=${days}`,
  );

export const stockOverviewData = (ticker: string) =>
  getJSON<OverviewData>(`/api/stock/overview-data?ticker=${ticker}`);

export const stockIncomeTrend = (ticker: string) =>
  getJSON<IncomeTrend>(`/api/stock/income-trend?ticker=${ticker}`);;

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
