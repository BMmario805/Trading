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
  trainedSymbols: string[]
  config: {
    universe: string[]
    workInterval: string
    trendInterval: string
    scanSeconds: number
    riskPct: number
    maxMargin: number
    maxEquity: number
    slAtr: number
    tpAtr: number
    trailAtr: number
    maxPositions: number
    maxHoldSeconds: number
    sentimentWeight: number
  }
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

export interface BotPerformance {
  points: PerfPoint[]
  summary: {
    trades: number
    wins: number
    losses: number
    winRate: number | null
    pnl: number
    best: number
    worst: number
  }
  symbols: string[]
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
