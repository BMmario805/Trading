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
