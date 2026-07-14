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
  trainedSymbols: string[]
  learning: BotLearning | null
  config: {
    universe: string[]
    workInterval: string
    trendInterval: string
    scanSeconds: number
    riskPct: number
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
