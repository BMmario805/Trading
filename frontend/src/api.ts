import type {
  Analysis,
  BotPerformance,
  BotStatus,
  BotTrade,
  Candle,
  IndicatorPanel,
  OpinionsResponse,
  Overlays,
  SentimentPoint,
  SymbolInfo,
  Tick,
  TradeDetail,
  TrainingStatus,
} from './types'

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${url} -> ${r.status}`)
  return r.json()
}

export const fetchSymbols = () => getJson<SymbolInfo[]>('/api/symbols')

export const fetchCandles = (id: string, interval: string) =>
  getJson<Candle[]>(`/api/candles/${id}?interval=${interval}`)

export const fetchAnalysis = (id: string, interval: string) =>
  getJson<Analysis>(`/api/analysis/${id}?interval=${interval}`)

export const fetchSentimentHistory = (id: string, hours = 72) =>
  getJson<SentimentPoint[]>(`/api/sentiment/${id}/history?hours=${hours}`)

export const fetchOpinions = (id: string) =>
  getJson<OpinionsResponse>(`/api/opinions/${id}`)

export const fetchOverlays = (id: string, interval: string) =>
  getJson<Overlays>(`/api/overlays/${id}?interval=${interval}`)

export const fetchIndicators = (id: string, interval: string) =>
  getJson<IndicatorPanel>(`/api/indicators/${id}?interval=${interval}`)

export const fetchTradeDetail = (botId: string, tradeId: number) =>
  getJson<TradeDetail>(`/api/bots/${botId}/trades/${tradeId}`)

export const fetchBots = () => getJson<BotStatus[]>('/api/bots')

export const fetchBotTrades = (id: string, limit = 30) =>
  getJson<BotTrade[]>(`/api/bots/${id}/trades?limit=${limit}`)

export async function botAction(
  id: string,
  action: 'start' | 'stop' | 'reset',
): Promise<BotStatus> {
  const r = await fetch(`/api/bots/${id}/${action}`, { method: 'POST' })
  if (!r.ok) throw new Error(`bot ${action} -> ${r.status}`)
  return r.json()
}

export async function startTraining(id: string): Promise<void> {
  const r = await fetch(`/api/bots/${id}/train`, { method: 'POST' })
  if (!r.ok) throw new Error(`bot train -> ${r.status}`)
}

export const fetchTraining = (id: string) =>
  getJson<TrainingStatus>(`/api/bots/${id}/training`)

export function fetchBotPerformance(filters: {
  bot?: string
  symbol?: string
  from?: number
  to?: number
  perBot?: boolean
  source?: 'paper' | 'mt5'
}): Promise<BotPerformance> {
  const q = new URLSearchParams()
  if (filters.bot) q.set('bot', filters.bot)
  if (filters.symbol) q.set('symbol', filters.symbol)
  if (filters.from) q.set('from', String(filters.from))
  if (filters.to) q.set('to', String(filters.to))
  if (filters.perBot) q.set('perBot', 'true')
  const qs = q.toString()
  return getJson<BotPerformance>(`${filters.source === 'mt5' ? '/api/mt5/performance' : '/api/bots/performance'}${qs ? `?${qs}` : ''}`)
}

/** WebSocket de precios con reconexión y resuscripción automáticas. */
export class PriceSocket {
  private ws: WebSocket | null = null
  private subscribed = new Set<string>()
  private listeners = new Set<(t: Tick) => void>()
  private closed = true

  /** Idempotente: conecta si está cerrado (soporta el remontaje de StrictMode). */
  open() {
    if (!this.closed) return
    this.closed = false
    this.connect()
  }

  private connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    this.ws = new WebSocket(`${proto}://${location.host}/ws`)
    this.ws.onopen = () => {
      for (const s of this.subscribed) this.send({ action: 'subscribe', symbol: s })
    }
    this.ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data)
      if (msg.type === 'tick') for (const fn of this.listeners) fn(msg as Tick)
    }
    this.ws.onclose = () => {
      if (!this.closed) setTimeout(() => this.connect(), 2000)
    }
  }

  private send(obj: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj))
  }

  subscribe(symbol: string) {
    if (this.subscribed.has(symbol)) return
    this.subscribed.add(symbol)
    this.send({ action: 'subscribe', symbol })
  }

  onTick(fn: (t: Tick) => void): () => void {
    this.listeners.add(fn)
    return () => {
      this.listeners.delete(fn)
    }
  }

  close() {
    this.closed = true
    this.ws?.close()
  }
}
