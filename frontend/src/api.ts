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
  AgentThought, DeskEvent, MakerPulse, PositionUpdate, SwarmStatusEvent, LiveEvent, SocketStatus,
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

/** Precios y telemetría, con recuperación de suscripciones y cierre seguro. */
export class PriceSocket {
  private ws: WebSocket | null = null
  private subscribed = new Set<string>()
  private listeners = new Set<(t: Tick) => void>()
  private handlers = new Map<string, Set<(event: LiveEvent) => void>>()
  private statusHandlers = new Set<(status: SocketStatus) => void>()
  private closed = true
  private generation = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | undefined
  private status: SocketStatus = 'disconnected'
  private streamId = ''
  private lastSequence = 0
  private retained: LiveEvent[] = []

  open() {
    if (!this.closed) return
    this.closed = false
    this.connect(++this.generation)
  }

  private setStatus(status: SocketStatus) {
    this.status = status
    for (const fn of this.statusHandlers) this.deliver(fn, status)
  }

  private deliver<T>(fn: (value: T) => void, value: T) {
    try { fn(value) } catch (error) { console.error('Error en una suscripción en vivo', error) }
  }

  private connect(generation: number) {
    if (this.closed || generation !== this.generation) return
    this.setStatus('connecting')
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/ws`)
    this.ws = ws
    ws.onopen = () => {
      if (this.closed || generation !== this.generation) { ws.close(); return }
      this.setStatus('connected')
      for (const symbol of this.subscribed) this.send({ action: 'subscribe', symbol })
    }
    ws.onmessage = (ev) => {
      if (generation !== this.generation) return
      try {
        const msg = JSON.parse(ev.data)
        if (!msg || typeof msg !== 'object') return
        if (msg.type === 'tick') {
          if (typeof msg.symbol === 'string' && Number.isFinite(msg.price))
            for (const fn of this.listeners) this.deliver(fn, msg as Tick)
          return
        }
        if (!Number.isFinite(msg.sequence) || typeof msg.streamId !== 'string') return
        if (msg.streamId !== this.streamId) { this.streamId = msg.streamId; this.lastSequence = 0; this.retained = [] }
        if (msg.sequence <= this.lastSequence) return
        this.lastSequence = msg.sequence
        if (msg.type === 'agent_thought') {
          this.retained = [...this.retained, msg as LiveEvent]
        } else if (msg.type !== 'role_update') {
          this.retained = [...this.retained.filter(row => row.type !== msg.type || ('desk' in row && row.desk !== msg.desk)), msg as LiveEvent]
        }
        this.retained = this.retained.slice(-100)
        for (const fn of this.handlers.get(msg.type) ?? []) this.deliver(fn, msg as LiveEvent)
      } catch { /* Un paquete inválido no interrumpe los siguientes mensajes. */ }
    }
    ws.onerror = () => ws.close()
    ws.onclose = () => {
      if (generation !== this.generation) return
      this.setStatus('disconnected')
      if (!this.closed) this.reconnectTimer = setTimeout(() => this.connect(generation), 2000)
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
    return () => { this.listeners.delete(fn) }
  }

  private on<T extends LiveEvent>(types: string[], fn: (event: T) => void): () => void {
    const handler = fn as (event: LiveEvent) => void
    for (const type of types) {
      if (!this.handlers.has(type)) this.handlers.set(type, new Set())
      this.handlers.get(type)!.add(handler)
    }
    for (const event of this.retained) if (types.includes(event.type)) this.deliver(handler, event)
    return () => { for (const type of types) this.handlers.get(type)?.delete(handler) }
  }

  onDeskEvent(fn: (event: DeskEvent) => void) { return this.on(['role_update', 'desk_snapshot'], fn) }
  onAgentThought(fn: (event: AgentThought) => void) { return this.on(['agent_thought'], fn) }
  onMakerPulse(fn: (event: MakerPulse) => void) { return this.on(['orderbook_pulse'], fn) }
  onPositionUpdate(fn: (event: PositionUpdate) => void) { return this.on(['position_update'], fn) }
  onSwarmStatus(fn: (event: SwarmStatusEvent) => void) { return this.on(['swarm_status'], fn) }

  onStatus(fn: (status: SocketStatus) => void): () => void {
    this.statusHandlers.add(fn)
    this.deliver(fn, this.status)
    return () => { this.statusHandlers.delete(fn) }
  }

  close() {
    this.closed = true
    ++this.generation
    clearTimeout(this.reconnectTimer)
    this.ws?.close()
    this.ws = null
    this.setStatus('disconnected')
  }
}
