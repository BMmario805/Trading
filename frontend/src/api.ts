import type { Analysis, Candle, SentimentPoint, SymbolInfo, Tick } from './types'

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
