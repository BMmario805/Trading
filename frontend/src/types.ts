export interface SymbolInfo {
  id: string
  label: string
  category: string
  source: string
  note: string
}

export interface SentimentPoint {
  ts: number
  bullishPct: number
  sample: number
}

export interface Candle {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface Tick {
  type: 'tick'
  symbol: string
  price: number
  changePct: number
  time: number
}

export interface SentimentSource {
  source: string
  detail: string
  bullish: number
  sample: number
  hasItems: boolean
}

export type OpinionBadge =
  | 'long'
  | 'short'
  | 'bullish'
  | 'bearish'
  | 'positive'
  | 'negative'
  | 'neutral'

export interface OpinionItem {
  user: string | null
  avatar: string | null
  text: string
  badge: OpinionBadge
  likes: number | null
  time: number | null
  url: string | null
  image: string | null
}

export interface OpinionSource {
  source: string
  detail: string
  items: OpinionItem[]
}

export interface OpinionsResponse {
  symbol: string
  updatedAt: number
  sources: OpinionSource[]
}

export interface Sentiment {
  symbol: string
  bullishPct: number
  bearishPct: number
  label: string
  sampleSize: number
  sources: SentimentSource[]
  errors: string[]
  updatedAt: number
}

export interface TechSignal {
  name: string
  value: string
  vote: number
}

export interface Analysis {
  symbol: string
  interval: string
  technical: { score: number; rsi: number | null; signals: TechSignal[] }
  sentiment: Sentiment
  upProbability: number
  verdict: 'alcista' | 'bajista' | 'neutral'
}

/* ---------- sistemas de trading dibujables (overlays) ---------- */

export interface OverlayPoint {
  time: number
  value: number
}

export interface SRLevel {
  price: number
  touches: number
  kind: 'soporte' | 'resistencia'
}

export interface Trendline {
  kind: 'soporte' | 'resistencia'
  direction: 'alcista' | 'bajista'
  from: { time: number; price: number }
  to: { time: number; price: number }
}

export interface FibonacciSwing {
  direction: 'alcista' | 'bajista'
  high: { time: number; price: number }
  low: { time: number; price: number }
  levels: { ratio: number; price: number }[]
}

export interface Overlays {
  sr: SRLevel[]
  trendlines: Trendline[]
  fibonacci: FibonacciSwing | null
  ema20: OverlayPoint[]
  ema50: OverlayPoint[]
  bollinger: {
    upper: OverlayPoint[]
    middle: OverlayPoint[]
    lower: OverlayPoint[]
  }
  supertrend?: OverlayPoint[]
  keltner?: {
    upper: OverlayPoint[]
    lower: OverlayPoint[]
  }
  donchian?: {
    upper: OverlayPoint[]
    lower: OverlayPoint[]
  }
  psar?: OverlayPoint[]
  ichimoku?: {
    spanA: OverlayPoint[]
    spanB: OverlayPoint[]
  }
  vwap?: OverlayPoint[]
  openingRange?: { high: number; low: number; date: number }
}

export interface Confirmation {
  system: string
  detail: string
  vote: number
}

export interface TradeSnapshot {
  interval: string
  score: number
  confirmations: Confirmation[]
  candles: Candle[]
  overlays: Overlays
}

export interface TradeDetail {
  id: number
  bot: string
  symbol: string
  direction: 'long' | 'short'
  qty: number
  entryPrice: number
  entryTime: number
  sl: number
  tp: number
  exitPrice: number | null
  exitTime: number | null
  exitReason: string | null
  pnl: number | null
  status: 'open' | 'closed'
  reasons: string[]
  snapshot: TradeSnapshot | null
}

export interface BotPosition {
  id: number
  symbol: string
  direction: 'long' | 'short'
  qty: number
  entryPrice: number
  entryTime: number
  sl: number
  tp: number
  lastPrice: number
  unrealizedPnl: number
  reasons: string[]
}

export interface BotTrade {
  id: number
  symbol: string
  direction: 'long' | 'short'
  qty: number
  entryPrice: number
  entryTime: number
  sl: number
  tp: number
  exitPrice: number
  exitTime: number
  exitReason: string
  pnl: number
  reasons: string[]
}

export interface BotStatus {
  id: string
  name: string
  horizon: string
  running: boolean
  initialBalance: number
  currency?: string
  session?: { initial: number; currency: string; startedAt: number } | null
  balance: number
  equity: number
  openPositions: BotPosition[]
  stats: {
    closedTrades: number
    wins: number
    losses: number
    winRate: number | null
    realizedPnl: number
  }
  risk: {
    dailyPnl: number
    dailyLossLimit: number
    dailyLossLimitPct: number
    entriesPaused: boolean
    consecLosses: number
    riskScale: number
    maxSameCategory: number
    overextAtr: number
  }
  diagnostics?: Record<string, { reason: string; at: number; score?: number }>
  trainedSymbols: string[]
  learning: BotLearning | null
  config: {
    strategyProfile?: string
    riskProfile?: string
    universe: string[]
    workInterval: string
    trendInterval: string
    scanSeconds: number
    riskPct: number
    requireValidation?: boolean
    probationSizePct?: number
    maxProbationPositions?: number
    maxLeverage: number
    slAtr: number
    tpAtr: number
    trailAtr: number
    maxPositions: number
    maxHoldSeconds: number
    sentimentWeight: number
  }
}

export interface LearningBlock {
  symbol: string
  direction: 'long' | 'short' | null
  reason: string
}

export interface BotLearning {
  blocks: LearningBlock[]
  sizeMult: Record<string, number>
  edges: Record<string, 'validated' | 'rejected' | 'unknown'>
  autoTrainPending?: string[]
  autoRetrainHours: number
  lastTrainedAt: number | null
  nextRetrainAt: number | null
  updatedAt: number | null
}

export interface TrainMetrics {
  trades: number
  wins: number
  pnl: number
  winRate: number | null
  profitFactor: number | null
  maxDrawdown: number
}

export interface TrainResult {
  symbol: string
  ok?: boolean
  reason?: string
  applied?: boolean
  params?: {
    entryThreshold: number
    slAtr: number
    tpAtr: number
    trailAtr: number
  }
  train?: TrainMetrics
  validation?: TrainMetrics
}

export interface SavedTrainResult {
  bot: string
  symbol: string
  params: NonNullable<TrainResult['params']>
  metrics: { train: TrainMetrics; validation: TrainMetrics }
  applied: boolean
  trainedAt: number
}

export interface PerfPoint {
  ts: number
  cumPnl: number
  pnl: number
  symbol: string
  bot: string
}

export interface PerfSummary {
  trades: number
  wins: number
  losses: number
  winRate: number | null
  pnl: number
  best: number
  worst: number
}

export interface PerfSeries {
  id: string
  name: string
  points: PerfPoint[]
  summary: PerfSummary
}

export interface BotPerformance {
  source?: 'mt5' | 'paper'
  currency?: string | null
  session?: { initial: number; currency: string; startedAt: number } | null
  updatedAt?: number
  syncError?: string | null
  cards?: { id: string; name: string; realizedPnl: number; unrealizedPnl: number; totalPnl: number; openPositions: number; closedTrades: number }[]
  accountHistory?: { ts: number; balance: number; equity: number }[]
  account?: { balance: number | null; equity: number | null; realizedPnl: number; unrealizedPnl: number; totalPnl: number; netFunding: number; reconciliationDifference?: number | null; historySince: number | null }

  points: PerfPoint[]
  summary: PerfSummary
  symbols: string[]
  bots: { id: string; name: string }[]
  series?: PerfSeries[]
}

/* ---------- subpanel de indicadores del gráfico ---------- */

export interface IndicatorPanel {
  symbol: string
  interval: string
  rsi: OverlayPoint[]
  macd: { line: OverlayPoint[]; signal: OverlayPoint[]; hist: OverlayPoint[] }
  stoch: { k: OverlayPoint[]; d: OverlayPoint[] }
  adx: OverlayPoint[]
  mfi: OverlayPoint[]
  cci: OverlayPoint[]
  obv: OverlayPoint[]
  roc: OverlayPoint[]
  atr: OverlayPoint[]
}

export interface TrainingStatus {
  running: boolean
  progress: string | null
  current: string | null
  results: TrainResult[]
  startedAt: number | null
  finishedAt: number | null
  saved: SavedTrainResult[]
}

export const CATEGORIES: Record<string, string> = {
  metales: 'Metales · Oro',
  cripto: 'Cripto',
  acciones: 'Acciones',
  indices: 'Índices',
  forex: 'Forex',
  futuros: 'Futuros',
}

export const INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d'] as const
export type Interval = (typeof INTERVALS)[number]

export const INTERVAL_SECONDS: Record<Interval, number> = {
  '1m': 60,
  '5m': 300,
  '15m': 900,
  '1h': 3600,
  '4h': 3600, // el backend sirve velas de 1h para "4h" en Yahoo
  '1d': 86400,
}

export interface DeskCandle { time: number; open: number; high: number; low: number; close: number }
export interface DeskRole { name: string; status: string; detail: string; provider: string; at?: number }
export interface QuantVerdict {
  approved: boolean; adjusted_risk_fraction: number; kelly_score: number; friction_warning: string; rationale: string
}
export interface MakerOrder {
  symbol: string; entryKey?: string; side: string; quantity: number; filled: number; expiresAt: number
  placedAt?: number; remainingSeconds?: number; pressure?: number | null; bookFresh?: boolean; status?: string
}
export interface MakerState {
  pending: MakerOrder[]; reservedCash: number; ttlSeconds?: number
  history?: { symbol: string; side: string; at: number; status: string; filled: number; quantity: number }[]
}
export interface LiveEnvelope { at: number; sequence: number; streamId: string }
export interface RoleUpdate extends LiveEnvelope {
  type: 'role_update'; desk: string; id: number; role: string; status: string; detail: string; provider: string
}
export interface DeskSnapshot extends LiveEnvelope { type: 'desk_snapshot'; desk: string; data: DeskState }
export type DeskEvent = RoleUpdate | DeskSnapshot
export interface AgentThought extends LiveEnvelope {
  type: 'agent_thought'; desk: string; symbol: string; agent: string; role: string; phase: string; detail: string
  approved?: boolean; conviction?: number; score?: number | null; verdict?: QuantVerdict
  report?: Record<string, unknown> | null
}
export interface MakerPulse extends LiveEnvelope { type: 'orderbook_pulse'; desk: string; maker: MakerState }
export interface PositionUpdate extends LiveEnvelope { type: 'position_update'; desk: string; data: OpenPositions }
export interface SwarmDecision {
  symbol: string; at: number; score: number | null; quorum: boolean; reason: string; profile: string
  pulse?: Record<string, unknown>; flow?: Record<string, unknown>; risk?: Record<string, unknown>; commander?: Record<string, unknown>
}
export interface SwarmStatus {
  transport?: 'api' | 'subscription'; requiresApiKeys?: boolean; billing?: 'api' | 'subscription'
  subscription?: { grokReady: boolean; chatgptReady: boolean; configured: boolean } | null
  configured: boolean; enabled: boolean; running?: boolean; mode: 'shadow' | 'paper'; armed: boolean
  xaiConfigured?: boolean; openaiConfigured?: boolean; remaining24h?: number
  estimatedCostMonthUSD?: number | null; monthlyCapUSD?: number; decisions: (SwarmDecision | null)[]
}
export interface SwarmStatusEvent extends LiveEnvelope { type: 'swarm_status'; data: SwarmStatus }
export type LiveEvent = DeskEvent | AgentThought | MakerPulse | PositionUpdate | SwarmStatusEvent
export type SocketStatus = 'connecting' | 'connected' | 'disconnected'
export interface Candidate { symbol: string; eligible: boolean; selected: boolean; change24h: number; volumeUSD: number; spreadBp: number; reason: string; qualityReason?: string; status?: string; checkedAt?: number | null; category?: string; base?: string; source?: string }
export interface OpenPosition {
  id: string; source: 'desk' | 'lab' | 'mt5'; sourceLabel: string; botId: string; botName: string
  symbol: string; side: 'long' | 'short'; quantity: number | null; entryPrice: number | null; currentPrice: number | null
  notional: number | null; unrealizedPnl: number | null; currency: string | null; openedAt: number | null
  stopLoss: number | null; takeProfit: number | null; priceFresh: boolean; accountMode: string
  quantityUnit: 'units' | 'lots'; pnlBasis: 'estimated-net' | 'gross' | 'broker-floating'; priceUpdatedAt: number | null
}
export interface PositionSource { source: 'desk' | 'lab' | 'mt5'; label: string; accountMode: 'paper' | 'demo'; available: boolean; updatedAt: number | null; stale: boolean; message: string | null }
export interface OpenPositions { positions: OpenPosition[]; sources: PositionSource[]; updatedAt: number }
export interface Trade { entryKey: string; symbol: string; entry: number; entryTime: number; status: string; notional: number; pnl?: number; realizedPnl?: number; partialDone?: boolean; exitReason?: string; exitTime?: number }
export interface DeskState {
  id: 'crypto' | 'forex' | 'stocks'; name: string; currency: 'USDT' | 'USD'; session: { open: boolean; label: string }
  enabled: boolean; equity: number; initial: number; cash: number; benchmark: number
  funding?: { currency: 'EUR'; amount: number; netQuotePerEuro: number; at: number } | null
  selected: string | null; benchmarkSymbol: string | null; examined: number; discarded: number; eligible?: number; filtered?: number; notSelected?: number; scope?: string
  filterReasons?: { reason: string; count: number }[]; roles: DeskRole[]
  scanBatchSize?: number; scanReviewed?: number; maxPositions?: number; scanIntervalSeconds?: number; candleInterval?: string; chartInterval?: string
  policy?: { strategyProfile?: string; riskProfile?: string; partialFraction?: number | null; maxNotionalPct: number; minNetRewardRisk: number; riskPct: number; totalRiskPct: number; maxHoldSeconds?: number; partialAtR?: number | null; trailAtr?: number; stopAtr?: number; trailAfterR?: number }
  maker?: MakerState
  monitoring?: { riskIndependent: boolean; riskError: string | null; quoteAt: number | null }
  positions?: { symbol: string }[]
  candidates: Candidate[]; candles: DeskCandle[]; trades: Trade[]
  position: null | { symbol: string; notional: number; risk: number; sl: number; tp: number; lastBid: number; targetIsReference?: boolean }
  stats: { closed: number; wins: number; pnl: number }
  experiment?: { version: string; closed: number; wins: number; pnl: number; opened: number }
  events: { id: number; at: number; role: string; status: string; detail: string }[]
  curve: { time: number; equity: number; benchmark: number }[]
  error: string | null; updatedAt: number | null
  gemini: null | { model: string; estimatedCostMonthUSD: number; monthlyCapUSD: number; remaining24h: number }
}
