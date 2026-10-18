import { lazy, memo, Suspense, useEffect, useRef, useState } from 'react'
import { CandlestickSeries, ColorType, createChart, type IChartApi, type ISeriesApi, type Time } from 'lightweight-charts'
import KitchenStage, { type KitchenRole } from './KitchenStage'
import ResearchPanel from './ResearchPanel'
import './DeskPanel.css'

const PerformanceReview = lazy(() => import('./PerformanceReview'))

interface Candle { time: number; open: number; high: number; low: number; close: number }
interface Candidate { symbol: string; eligible: boolean; selected: boolean; change24h: number; volumeUSD: number; spreadBp: number; reason: string; qualityReason?: string; status?: string; checkedAt?: number | null; category?: string; base?: string; source?: string }
interface OpenPosition {
  id: string; source: 'desk' | 'lab' | 'mt5'; sourceLabel: string; botId: string; botName: string
  symbol: string; side: 'long' | 'short'; quantity: number | null; entryPrice: number | null; currentPrice: number | null
  notional: number | null; unrealizedPnl: number | null; currency: string | null; openedAt: number | null
  stopLoss: number | null; takeProfit: number | null; priceFresh: boolean; accountMode: string
  quantityUnit: 'units' | 'lots'; pnlBasis: 'estimated-net' | 'gross' | 'broker-floating'; priceUpdatedAt: number | null
}
interface PositionSource { source: 'desk' | 'lab' | 'mt5'; label: string; accountMode: 'paper' | 'demo'; available: boolean; updatedAt: number | null; stale: boolean; message: string | null }
interface OpenPositions { positions: OpenPosition[]; sources: PositionSource[]; updatedAt: number }
interface Trade { entryKey: string; symbol: string; entry: number; entryTime: number; status: string; notional: number; pnl?: number; exitReason?: string; exitTime?: number }
interface Desk {
  id: 'crypto' | 'forex' | 'stocks'; name: string; currency: 'USDT' | 'USD'; session: { open: boolean; label: string }
  enabled: boolean; equity: number; initial: number; cash: number; benchmark: number
  funding?: { currency: 'EUR'; amount: number; netQuotePerEuro: number; at: number } | null
  selected: string | null; benchmarkSymbol: string | null; examined: number; discarded: number; eligible?: number; filtered?: number; notSelected?: number; scope?: string
  filterReasons?: { reason: string; count: number }[]; roles: KitchenRole[]
  scanBatchSize?: number; scanReviewed?: number; maxPositions?: number; scanIntervalSeconds?: number; candleInterval?: string
  policy?: { maxNotionalPct: number; minNetRewardRisk: number; riskPct: number; totalRiskPct: number }
  monitoring?: { riskIndependent: boolean; riskError: string | null; quoteAt: number | null }
  positions?: { symbol: string }[]
  candidates: Candidate[]; candles: Candle[]; trades: Trade[]
  position: null | { symbol: string; notional: number; risk: number; sl: number; tp: number; lastBid: number }
  stats: { closed: number; wins: number; pnl: number }
  experiment?: { version: string; closed: number; wins: number; pnl: number; opened: number }
  events: { id: number; at: number; role: string; status: string; detail: string }[]
  curve: { time: number; equity: number; benchmark: number }[]
  error: string | null; updatedAt: number | null
  gemini: null | { model: string; estimatedCostMonthUSD: number; monthlyCapUSD: number; remaining24h: number }
}

const PURPOSES: Record<string, string> = { Selector: 'Escoge el ingrediente', Tape: 'Lee la temperatura', Price: 'Busca el punto de entrada', Wire: 'Revisa las fuentes', Size: 'Mide las cantidades', Exec: 'Sirve la orden simulada', Risk: 'Cuida cada posición' }
const STATES: Record<string, string> = { waiting: 'En espera', selected: 'Seleccionado', ready: 'Preparado', blocked: 'Bloqueado', corroborated: 'Referencia corroborada', managing: 'Gestionando', executed: 'Ejecutado', closed: 'Cerrado' }
const SCAN_STATES: Record<string, string> = { pending: 'Por revisar', filtered: 'Filtrado', waiting: 'Sin señal', blocked: 'Sin entrada', error: 'Datos pendientes', executed: 'Ejecutado', managing: 'Gestionando' }
const money = (value: number) => value.toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const timeOf = (at: number) => new Date(at * 1000).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
const shortSymbol = (symbol: string) => symbol.replace(/USDT$/, '')
const statusOf = (status: string) => STATES[status] ?? status
const priceOf = (value: number | null) => value === null || !Number.isFinite(value) ? '—' : value.toLocaleString('es-ES', { maximumSignificantDigits: 8 })
const dateOf = (at: number) => new Date(at * 1000).toLocaleString('es-ES', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
const accountOf = (mode: string) => ({ paper: 'Simulación', simulated: 'Simulación', simulation: 'Simulación', demo: 'Cuenta demo', real: 'Cuenta real', live: 'Cuenta real' }[mode] ?? mode)

function ChefMark() {
  return <svg viewBox="0 0 40 40" fill="none" aria-hidden="true"><path d="M11 24v8c5 3 13 3 18 0v-8M11 25c-8-1-8-12-1-13 1-8 13-10 17-3 9-1 12 13 2 16H11Z" fill="currentColor" /><path d="M13 28h14" stroke="#e66b41" strokeWidth="2" strokeLinecap="round" /></svg>
}

function MarketChart({ candles }: { candles: Candle[] }) {
  const element = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)
  const series = useRef<ISeriesApi<'Candlestick'> | null>(null)
  useEffect(() => {
    if (!element.current) return
    chart.current = createChart(element.current, { autoSize: true, height: 320,
      layout: { background: { type: ColorType.Solid, color: '#fffcf5' }, textColor: '#8a8070' },
      grid: { vertLines: { color: '#f0eade' }, horzLines: { color: '#f0eade' } },
      timeScale: { timeVisible: true, borderColor: '#e5ddce' }, rightPriceScale: { borderColor: '#e5ddce' } })
    series.current = chart.current.addSeries(CandlestickSeries, { upColor: '#4c9377', downColor: '#df795e', borderVisible: false, wickUpColor: '#4c9377', wickDownColor: '#df795e' })
    return () => { chart.current?.remove(); chart.current = null; series.current = null }
  }, [])
  useEffect(() => {
    series.current?.setData(candles.map(c => ({ ...c, time: c.time as Time })))
    chart.current?.timeScale().fitContent()
  }, [candles])
  return <div className="desk-candles" ref={element} aria-label="Gráfico de velas del mercado seleccionado" />
}

function EquityChart({ rows }: { rows: Desk['curve'] }) {
  if (rows.length < 2) return <div className="desk-empty-curve">El rendimiento aparecerá cuando haya suficientes datos registrados.</div>
  const values = rows.flatMap(r => [r.equity, r.benchmark])
  const lo = Math.min(...values) - 1, hi = Math.max(...values) + 1
  const points = (key: 'equity' | 'benchmark') => rows.map((r, i) => `${20 + i / (rows.length - 1) * 960},${140 - (r[key] - lo) / (hi - lo) * 115}`).join(' ')
  return <svg viewBox="0 0 1000 165" role="img" aria-label="Patrimonio de la mesa y referencia comprar y mantener">
    {[25, 65, 105, 145].map(y => <line key={y} x1="20" x2="980" y1={y} y2={y} stroke="#e9e3d8" />)}
    <polyline points={points('benchmark')} fill="none" stroke="#b4a58a" strokeWidth="2" strokeDasharray="5 5" />
    <polyline points={points('equity')} fill="none" stroke="#4c9377" strokeWidth="3" />
  </svg>
}

function OpenPositionsPanel() {
  const [data, setData] = useState<OpenPositions | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false, pending = false
    const load = async () => {
      if (pending) return
      pending = true
      try {
        const response = await fetch('/api/positions/open')
        if (!response.ok) throw new Error('unavailable')
        const value: OpenPositions = await response.json()
        if (!cancelled) { setData(value); setError(null) }
      } catch { if (!cancelled) setError('No se pudieron actualizar las operaciones. Se conserva la última lectura disponible.') }
      finally { pending = false }
    }
    void load()
    const timer = window.setInterval(() => { void load() }, 5_000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [])
  return <section className="kitchen-open-positions" aria-labelledby="kitchen-positions-title">
    <div className="kitchen-section-title"><div><span className="kitchen-eyebrow">LOS PLATOS EN MARCHA</span><h2 id="kitchen-positions-title">Operaciones abiertas <span className="kitchen-count">{data?.positions.length ?? '—'}</span></h2><p>Mesa Siete, Laboratorio y MT5, reunidos en una misma vista.</p></div>{data && <time>Lectura {timeOf(data.updatedAt)}</time>}</div>
    {error && <p className="kitchen-error" role="status">{error}</p>}
    {data && <div className="kitchen-source-statuses">{data.sources.map(source => <div className={`kitchen-source-status ${error || !source.available || source.stale ? 'is-stale' : ''}`} key={source.source}><span className="kitchen-source-dot" /><strong>{source.label}</strong><span>{accountOf(source.accountMode)}</span><b>{!source.available ? 'Sin conexión' : error || source.stale ? 'Última lectura' : 'Actualizado'}</b>{source.message && <small>{source.message}</small>}</div>)}</div>}
    {!data ? <p className="kitchen-empty" role="status">{error ? 'Aún no hay una lectura de operaciones disponible.' : 'Consultando las operaciones abiertas…'}</p> : data.positions.length === 0 ? <p className="kitchen-empty">No hay operaciones abiertas en las fuentes disponibles.</p> : <div className="kitchen-positions-scroll"><table className="kitchen-positions-table"><thead><tr><th>Mercado / origen</th><th>Posición</th><th>Entrada</th><th>Precio actual</th><th>Nominal</th><th>Resultado abierto</th><th>Stop / objetivo</th><th>Apertura</th></tr></thead><tbody>{data.positions.map(position => {
      const currency = position.currency ?? ''
      const pnl = position.unrealizedPnl
      const priceFresh = position.priceFresh && !error
      return <tr key={`${position.source}-${position.id}`} className={position.source === 'desk' ? 'kitchen-desk-position' : ''}>
        <td><strong>{position.symbol}</strong><span className="kitchen-position-meta">{position.sourceLabel} · {position.botName}</span><span className="kitchen-account-pill">{accountOf(position.accountMode)}</span></td>
        <td><span className={`kitchen-side ${position.side === 'short' ? 'is-short' : ''}`}>{position.side === 'short' ? 'Venta' : 'Compra'}</span><span className="kitchen-position-meta">{priceOf(position.quantity)} {position.quantityUnit === 'lots' ? 'lotes' : 'unidades'}</span></td>
        <td>{priceOf(position.entryPrice)}</td>
        <td>{priceOf(position.currentPrice)}<span className={`kitchen-price-state ${priceFresh ? '' : 'is-stale'}`}>{position.currentPrice === null ? 'Sin cotización' : priceFresh ? 'Precio actualizado' : 'Último precio guardado'}{position.priceUpdatedAt && <small>{timeOf(position.priceUpdatedAt)}</small>}</span></td>
        <td>{position.notional === null ? '—' : `${money(position.notional)} ${currency}`}</td>
        <td><strong className={pnl === null ? '' : pnl >= 0 ? 'up' : 'down'}>{pnl === null ? 'Sin valoración' : `${pnl >= 0 ? '+' : ''}${money(pnl)} ${currency}`}</strong><span className="kitchen-position-meta">{position.pnlBasis === 'estimated-net' ? 'Neto estimado' : position.pnlBasis === 'broker-floating' ? 'Flotante del bróker' : 'Bruto estimado'}</span></td>
        <td><span className="kitchen-position-stop"><small>SL</small> {priceOf(position.stopLoss)}</span><span className="kitchen-position-stop"><small>TP</small> {priceOf(position.takeProfit)}</span></td>
        <td>{position.openedAt ? dateOf(position.openedAt) : '—'}</td>
      </tr>
    })}</tbody></table></div>}
    <p className="kitchen-position-note">Cada resultado conserva su moneda. Los precios sin actualizar aparecen señalados; los importes de distintas divisas no se suman.</p>
  </section>
}

function AvailableMarkets({ desk }: { desk: Desk }) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<'all' | 'eligible' | 'filtered'>('all')
  const [limit, setLimit] = useState(50)
  const eligible = desk.eligible ?? desk.candidates.filter(candidate => candidate.eligible).length
  const filtered = desk.filtered ?? desk.candidates.filter(candidate => !candidate.eligible).length
  const reserve = desk.notSelected ?? desk.candidates.filter(candidate => candidate.eligible && !candidate.selected).length
  const rows = desk.candidates.filter(candidate => (filter === 'all' || (filter === 'eligible' ? candidate.eligible : !candidate.eligible)) && candidate.symbol.toLowerCase().includes(query.trim().toLowerCase()))
  return <section className="kitchen-markets" aria-labelledby="kitchen-markets-title">
    <div className="kitchen-section-title"><div><span className="kitchen-eyebrow">UNA DESPENSA MÁS GRANDE</span><h2 id="kitchen-markets-title">Mercados disponibles</h2><p>{desk.scope ?? 'Binance Spot · USDT'} · Hasta {desk.maxPositions ?? 3} posiciones simultáneas en Mesa Siete, en mercados distintos.</p></div><span className="kitchen-market-total">{desk.examined} revisados</span></div>
    <div className="kitchen-market-summary"><div className="is-eligible"><strong>{eligible}</strong><span>Aptos por liquidez<small>Elegibles para buscar una señal</small></span></div><div><strong>{reserve}</strong><span>En reserva<small>Disponibles, sin ser la selección actual</small></span></div><div className="is-filtered"><strong>{filtered}</strong><span>Filtrados<small>Por condiciones de mercado</small></span></div></div>
    {desk.scanBatchSize !== undefined && <p className="kitchen-scan-progress"><span aria-hidden="true">⌁</span>{(desk.positions?.length ?? (desk.position ? 1 : 0)) >= (desk.maxPositions ?? 3) ? 'Cupo completo; el equipo gestiona las salidas.' : <><strong>Último lote: {desk.scanReviewed ?? 0} mercados analizados.</strong> {desk.enabled ? `El equipo recorre lotes de ${desk.scanBatchSize} cada ${desk.scanIntervalSeconds ?? 20} segundos.` : 'La búsqueda está pausada.'}</>} Ser elegible no implica una señal de entrada.</p>}
    {!!desk.filterReasons?.length && <div className="kitchen-filter-reasons"><span>Motivos de filtrado</span>{desk.filterReasons.map(item => <span key={item.reason}><b>{item.count}</b> {item.reason}</span>)}</div>}
    <div className="kitchen-market-tools"><label><span className="kitchen-sr-only">Buscar mercado</span><span aria-hidden="true">⌕</span><input type="search" placeholder="Buscar mercado…" value={query} onChange={event => { setQuery(event.target.value); setLimit(50) }} /></label><div role="group" aria-label="Filtrar mercados">{([['all', 'Todos'], ['eligible', 'Elegibles'], ['filtered', 'Filtrados']] as const).map(([key, label]) => <button key={key} aria-pressed={filter === key} onClick={() => { setFilter(key); setLimit(50) }}>{label}</button>)}</div><span>{Math.min(limit, rows.length)} de {rows.length}</span></div>
    <div className="kitchen-market-table-scroll"><table><thead><tr><th>Mercado</th><th>24 h</th><th>Volumen 24 h</th><th>Diferencial</th><th>Aptitud del mercado</th><th>Último análisis</th></tr></thead><tbody>{rows.slice(0, limit).map(candidate => <tr key={candidate.symbol} className={candidate.selected ? 'desk-selected-row' : ''}><td><strong>{shortSymbol(candidate.symbol)}<small className="kitchen-quote"> / {desk.currency}</small></strong>{candidate.category && <small className="kitchen-position-meta">{candidate.category}</small>}</td><td className={candidate.change24h >= 0 ? 'up' : 'down'}>{candidate.change24h > 0 ? '+' : ''}{candidate.change24h.toFixed(2)} %</td><td>{candidate.volumeUSD >= 1e6 ? `${(candidate.volumeUSD / 1e6).toFixed(1)} M` : `${Math.round(candidate.volumeUSD / 1e3)} mil`} {desk.currency}</td><td>{Number.isFinite(candidate.spreadBp) ? `${candidate.spreadBp.toFixed(1)} pb` : '—'}</td><td><span className={`kitchen-table-pill ${candidate.selected ? 'is-selected' : candidate.eligible ? 'is-eligible' : 'is-filtered'}`}>{candidate.selected ? 'Elegido' : candidate.eligible ? 'En reserva · elegible' : 'Filtrado'}</span><small className="kitchen-candidate-reason">{candidate.qualityReason ?? (!candidate.eligible ? candidate.reason : 'Cotización y liquidez aptas')}</small></td><td><span className="kitchen-scan-state">{candidate.status ? SCAN_STATES[candidate.status] ?? candidate.status : candidate.eligible ? 'Por revisar' : 'Filtrado'}</span><small className="kitchen-candidate-reason">{candidate.reason}</small>{candidate.checkedAt && <time className="kitchen-position-meta">{timeOf(candidate.checkedAt)}</time>}</td></tr>)}</tbody></table>{rows.length === 0 && <p className="kitchen-empty">No hay mercados que coincidan con esta búsqueda.</p>}</div>
    {rows.length > limit && <button className="kitchen-show-more" onClick={() => setLimit(current => current + 50)}>Ver 50 mercados más <span aria-hidden="true">↓</span></button>}
  </section>
}

function DeskPanel({ market }: { market: Desk['id'] }) {
  const [desk, setDesk] = useState<Desk | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [openRole, setOpenRole] = useState('Selector')
  const [motionEnabled, setMotionEnabled] = useState(() => !window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  const [view, setView] = useState<'market' | 'performance' | 'history'>('market')
  const [notebookOpen, setNotebookOpen] = useState(false)
  const [newEvents, setNewEvents] = useState<Set<number>>(new Set())
  const [announcement, setAnnouncement] = useState('')
  const seenEvents = useRef<Set<number> | null>(null)
  const eventTimer = useRef<number | undefined>(undefined)
  const chartDetails = useRef<HTMLDetailsElement>(null)
  useEffect(() => {
    let cancelled = false, pending = false
    const load = async () => {
      if (pending) return
      pending = true
      try {
        const response = await fetch(`/api/desks/${market}`)
        if (!response.ok) throw new Error('unavailable')
        const value: Desk = await response.json()
        if (!cancelled) {
          if (seenEvents.current) {
            const fresh = value.events.filter(event => !seenEvents.current!.has(event.id))
            if (fresh.length) {
              setNewEvents(new Set(fresh.map(event => event.id)))
              setAnnouncement(`${fresh[0].role}: ${fresh[0].detail}`)
              window.clearTimeout(eventTimer.current)
              eventTimer.current = window.setTimeout(() => setNewEvents(new Set()), 2_800)
            }
          }
          seenEvents.current = new Set(value.events.map(event => event.id))
          setDesk(value); setError(null)
        }
      } catch { if (!cancelled) setError('No se pudo actualizar la mesa. Se conservan los últimos datos.') }
      finally { pending = false }
    }
    void load()
    const timer = window.setInterval(() => { void load() }, 5_000)
    return () => { cancelled = true; window.clearInterval(timer); window.clearTimeout(eventTimer.current) }
  }, [market])
  const action = async (name: string) => {
    setBusy(true)
    try {
      const response = await fetch(`/api/desks/${market}/${name}`, { method: 'POST' })
      if (!response.ok) throw new Error('failed')
      setDesk(await response.json())
    } catch { setError('No se pudo aplicar el control. Comprueba el estado antes de repetirlo.') }
    finally { setBusy(false) }
  }
  if (!desk) return <main className="desk-main kitchen-dashboard"><div className="kitchen-loading" role="status"><span className="kitchen-logo"><ChefMark /></span><h1>Encendiendo los fogones…</h1><p>{error ?? 'Conectando con los siete puestos de la mesa.'}</p></div></main>
  const activeRole = desk.roles.find(role => role.name === openRole)
  const net = desk.equity - desk.initial
  const euroRate = desk.funding?.netQuotePerEuro ?? 0
  const selected = desk.selected ? shortSymbol(desk.selected) : null
  const showMarket = () => {
    setView('market')
    if (chartDetails.current) { chartDetails.current.open = true; chartDetails.current.scrollIntoView({ behavior: motionEnabled ? 'smooth' : 'instant', block: 'start' }) }
  }
  return <main className={`desk-main kitchen-dashboard ${motionEnabled ? '' : 'kitchen-motion-paused'}`}>
    <span className="kitchen-sr-only" role="status" aria-live="polite">{announcement}</span>
    <header className="kitchen-header">
      <div className="kitchen-heading"><span className="kitchen-logo"><ChefMark /></span><div><span className="kitchen-eyebrow">MESA SIETE / {desk.name.toUpperCase()}</span><h1>La cocina de {desk.name.toLowerCase()}<span>.</span></h1><p>Siete especialistas · {desk.session.label} · Cuenta independiente</p></div></div>
      <div className="kitchen-top-controls">
        <button className="kitchen-motion-button" aria-pressed={!motionEnabled} onClick={() => setMotionEnabled(enabled => !enabled)} title="Solo cambia las animaciones, no la operativa"><span aria-hidden="true">{motionEnabled ? 'Ⅱ' : '▷'}</span>{motionEnabled ? 'Pausar animaciones' : 'Activar animaciones'}</button>
        <button className={`kitchen-trading-button ${desk.enabled ? '' : 'is-paused'}`} disabled={busy} onClick={() => { void action(desk.enabled ? 'pause' : 'start') }}><span aria-hidden="true">{desk.enabled ? 'Ⅱ' : '▷'}</span>{busy ? 'Aplicando…' : desk.enabled ? 'Pausar entradas' : 'Activar entradas'}</button>
      </div>
    </header>
    <div className="kitchen-status-line"><div><span className="kitchen-simulated">SIMULACIÓN</span><span className={`kitchen-live ${desk.enabled ? 'is-on' : ''}`}><i />{desk.enabled ? desk.session.open ? 'Cocina activa' : 'Esperando apertura' : 'Entradas pausadas'}</span><span className="kitchen-status-explanation">Datos reales · órdenes simuladas</span><span className="kitchen-status-explanation">{desk.positions?.length ?? (desk.position ? 1 : 0)}/{desk.maxPositions ?? 3} posiciones en marcha</span></div><time>{desk.updatedAt ? `Actualizado ${timeOf(desk.updatedAt)}` : 'Esperando actualización'}</time></div>
    {(error || desk.error) && <p className="kitchen-error" role="status">{error || desk.error}</p>}
    {market !== 'crypto' && <p className="kitchen-session-note">Cuenta simulada independiente de 100 USD. Precios de referencia de Yahoo; diferencial, costes y fracciones de acciones son supuestos de simulación. Las entradas requieren sesión y precio recientes.</p>}
    {desk.experiment && <p className="kitchen-session-note">Desde el último ajuste: {desk.experiment.opened} posiciones nuevas abiertas · {desk.experiment.closed} cerradas · {desk.experiment.wins} ganadoras · {money(desk.experiment.pnl)} {desk.currency} realizados. Estrategias en evaluación.</p>}
    <section className="kitchen-hud" aria-label="Estado de la cuenta simulada">
      <div className="kitchen-stat kitchen-stat-balance"><span className="kitchen-stat-symbol" aria-hidden="true">◈</span><div><small>{euroRate ? 'Capital · al cambio inicial' : 'Capital en la cocina'}</small><strong>{money(euroRate ? desk.equity / euroRate : desk.equity)} <em>{euroRate ? 'EUR' : desk.currency}</em></strong><span className={net >= 0 ? 'up' : 'down'}>{net >= 0 ? '+' : ''}{money(euroRate ? net / euroRate : net)} <b>({desk.initial ? (net / desk.initial * 100).toFixed(2) : '0.00'} %)</b></span>{euroRate > 0 && <span>{money(desk.equity)} {desk.currency} en la simulación</span>}</div></div>
      <div className="kitchen-stat"><span className="kitchen-stat-symbol" aria-hidden="true">◎</span><div><small>Despensa de mercados</small><strong>{desk.examined} <em>mercados</em></strong><span>{desk.eligible ?? desk.candidates.filter(candidate => candidate.eligible).length} aptos · {desk.filtered ?? desk.candidates.filter(candidate => !candidate.eligible).length} filtrados</span></div></div>
      <div className="kitchen-stat"><span className="kitchen-stat-symbol" aria-hidden="true">▱</span><div><small>Operaciones terminadas</small><strong>{desk.stats.closed} <em>operaciones</em></strong><span>{desk.stats.wins} ganadoras · {money(desk.stats.pnl)} {desk.currency}</span></div></div>
      <div className="kitchen-stat"><span className="kitchen-stat-symbol" aria-hidden="true">✦</span><div><small>IA compartida · proyecto</small><strong>{money(desk.gemini?.estimatedCostMonthUSD ?? 0)} <em>USD / mes</em></strong><span>Estimación · límite {desk.gemini?.monthlyCapUSD ?? 10} USD</span></div></div>
    </section>
    {euroRate > 0 && <p className="kitchen-session-note">Sesión desde {money(desk.funding!.amount)} € · Cambio EUR de referencia fijado al inicio: 1 € = {euroRate.toFixed(6)} {desk.currency} netos, según la fuente de referencia. Las operaciones y el gráfico se contabilizan en {desk.currency}. Riesgo objetivo {desk.policy?.riskPct ?? 0.5} %, exposición máxima {money(desk.policy?.maxNotionalPct ?? 100 / 6)} % por entrada. Objetivo neto mínimo: {desk.policy?.minNetRewardRisk ?? 1} veces el riesgo neto.</p>}
    <div className="kitchen-hero-grid">
      <section className="kitchen-workspace" aria-label="Los siete chefs y el proceso de la mesa">
        <div className="kitchen-section-head"><div><span className="kitchen-eyebrow">EL EQUIPO, EN DIRECTO</span><h2>Cada chef tiene su punto.</h2></div><span className="kitchen-scene-hint"><i />Toca un puesto para verlo</span></div>
        <KitchenStage roles={desk.roles} selectedSymbol={desk.selected} hasPosition={Boolean(desk.position)} enabled={desk.enabled && desk.session.open} openRole={openRole} onSelectRole={setOpenRole} motionEnabled={motionEnabled && !error && !desk.error} />
        {activeRole && <div className={`kitchen-role-detail role-${activeRole.status}`} key={activeRole.name}><div className="kitchen-role-number">{String(desk.roles.findIndex(role => role.name === openRole) + 1).padStart(2, '0')}</div><div className="kitchen-role-copy"><div><strong>{activeRole.name}</strong><span>{PURPOSES[activeRole.name]}</span><b className={`kitchen-state state-${activeRole.status}`}>{statusOf(activeRole.status)}</b></div><p>{activeRole.detail}</p><small>{activeRole.provider}{activeRole.at ? ` · ${timeOf(activeRole.at)}` : ''}</small></div></div>}
      </section>
      <aside className="kitchen-order-wrap" aria-label="Pedido y controles de la posición simulada">
        <section className="kitchen-order"><div className="kitchen-ticket-clip" aria-hidden="true" /><div className="kitchen-order-top"><span className="kitchen-eyebrow">LA COMANDA</span><span className="kitchen-order-number">MESA / 07</span></div><div className="kitchen-order-title"><span className="kitchen-coin" aria-hidden="true">{selected?.slice(0, 1) ?? '·'}</span><div><h2>{selected ?? 'Por elegir'}{selected && <small> / {desk.currency}</small>}</h2><p>{desk.position ? 'Posición simulada abierta' : selected ? 'En estudio para una entrada' : 'El selector busca un mercado'}</p></div></div>
          <div className={`kitchen-order-state ${desk.position ? 'is-managing' : ''}`}><span aria-hidden="true">{desk.position ? '◉' : '◌'}</span><div><strong>{desk.position ? 'Al cuidado de Risk' : desk.enabled ? 'Preparando la receta' : 'En pausa'}</strong><small>{desk.position ? 'Stops y objetivos bajo supervisión' : desk.enabled ? 'Cada control debe dar su visto bueno' : 'Las nuevas entradas están desactivadas'}</small></div></div>
          <div className="kitchen-ticket-divider"><span>LOS SIETE PASOS</span></div>
          <ol className="kitchen-recipe">{desk.roles.map((role, index) => <li key={role.name}><button className={openRole === role.name ? 'is-selected' : ''} onClick={() => setOpenRole(role.name)} aria-pressed={openRole === role.name}><span className={`kitchen-recipe-dot state-${role.status}`}>{['ready', 'selected', 'corroborated', 'executed', 'closed'].includes(role.status) ? '✓' : String(index + 1)}</span><strong>{role.name}</strong><span className={`kitchen-recipe-state state-${role.status}`}>{statusOf(role.status)}</span></button></li>)}</ol>
          <div className="kitchen-ticket-divider"><span>{desk.position ? 'POSICIÓN EN MARCHA' : 'LA DESPENSA'}</span></div>
          <dl className="kitchen-order-facts">{desk.position ? <><div><dt>Capital asignado</dt><dd>{money(desk.position.notional)} {desk.currency}</dd></div><div><dt>Riesgo previsto</dt><dd>{money(desk.position.risk)} {desk.currency}</dd></div><div><dt>Stop</dt><dd>{desk.position.sl.toPrecision(6)}</dd></div><div><dt>Objetivo</dt><dd>{desk.position.tp.toPrecision(6)}</dd></div></> : <><div><dt>Disponible en efectivo</dt><dd>{money(desk.cash)} {desk.currency}</dd></div><div><dt>Marco de análisis</dt><dd>Velas de {desk.candleInterval ?? '5m'}</dd></div><div><dt>Estado</dt><dd>Sin posición en este mercado</dd></div></>}</dl>
          <button className="kitchen-market-link" onClick={showMarket}>Ver el análisis del mercado <span aria-hidden="true">↗</span></button>
          <div className="kitchen-ticket-bottom"><span className="kitchen-ticket-bars" aria-hidden="true" /><span>EXPERIMENTO · SIN VENTAJA DEMOSTRADA</span></div>
        </section>
        <div className="kitchen-position-control"><span>Control de la cuenta simulada</span><button disabled={busy || !(desk.positions?.length ?? (desk.position ? 1 : 0))} onClick={() => { void action('close') }}>Cerrar todas y pausar <span aria-hidden="true">↗</span></button></div>
      </aside>
    </div>
    {market === 'crypto' && <Suspense fallback={<p className="kitchen-session-note">Preparando el diagnóstico económico…</p>}><PerformanceReview /></Suspense>}
    <OpenPositionsPanel />
    {market === 'crypto' && <ResearchPanel />}
    <AvailableMarkets desk={desk} />
    <section className="kitchen-activity" aria-label="Últimos procesos registrados"><div className="kitchen-activity-head"><span className="kitchen-activity-icon" aria-hidden="true">⌁</span><div><h2>Recién salido de cocina</h2><p>Lo que está haciendo el equipo.</p></div><span className="kitchen-live-note">Registro del motor</span></div><div className="kitchen-activity-grid">{desk.events.length ? desk.events.slice(0, 3).map(event => <article className={`kitchen-activity-item ${newEvents.has(event.id) ? 'is-new' : ''}`} key={event.id}><div><span className={`kitchen-event-dot state-${event.status}`} /><strong>{event.role}</strong><time>{timeOf(event.at)}</time></div><p>{event.detail}</p><span className={`kitchen-event-status state-${event.status}`}>{statusOf(event.status)}</span></article>) : <p className="kitchen-empty">Todavía no hay decisiones registradas. El equipo está esperando datos.</p>}</div></section>
    <details className="kitchen-notebook" ref={chartDetails} onToggle={event => setNotebookOpen(event.currentTarget.open)}><summary><span className="kitchen-notebook-icon" aria-hidden="true">▤</span><div><strong>El cuaderno de la mesa</strong><span>Mercados, gráficos y todas las decisiones, al detalle.</span></div><span className="kitchen-notebook-chevron" aria-hidden="true">↓</span></summary><div className="kitchen-notebook-body"><div className="kitchen-detail-tabs" role="tablist" aria-label="Detalles de la mesa">{([['market', 'Mercado y candidatos'], ['performance', 'Rendimiento'], ['history', 'Historial de la mesa']] as const).map(([key, label]) => <button key={key} id={`kitchen-tab-${key}`} role="tab" aria-selected={view === key} aria-controls={`kitchen-panel-${key}`} onClick={() => setView(key)}>{label}</button>)}</div>
      {notebookOpen && view === 'market' && <div className="desk-market-row kitchen-chart-wide" id="kitchen-panel-market" role="tabpanel" aria-labelledby="kitchen-tab-market"><section className="desk-box"><div className="desk-box-head"><h2>{desk.selected ?? 'Esperando selección'} <span className="desk-caption">/ SPOT · {desk.candleInterval ?? '5m'}</span></h2><span>Velas cerradas</span></div><MarketChart candles={desk.candles} />{desk.position ? <div className="desk-position"><span>Posición en {desk.position.symbol}</span><span>Capital: {money(desk.position.notional)} {desk.currency}</span><span>Último bid: {desk.position.lastBid.toPrecision(6)}</span></div> : <p className="kitchen-explanatory">En efectivo. La mesa espera que sus controles permitan una entrada.</p>}</section></div>}
      {notebookOpen && view === 'performance' && <section className="desk-box desk-equity" id="kitchen-panel-performance" role="tabpanel" aria-labelledby="kitchen-tab-performance"><div className="desk-box-head"><h2>Rendimiento · últimas 24 horas</h2><span><i className="desk-legend-strategy" /> Mesa <i className="desk-legend-hold" /> Comprar y mantener {desk.benchmarkSymbol ?? ''}</span></div><EquityChart rows={desk.curve} /><p className="kitchen-explanatory">Mismo capital inicial y comisiones. Referencia con 100 % invertido desde la selección; exposición distinta de la mesa.</p></section>}
      {notebookOpen && view === 'history' && <div className="desk-bottom-row" id="kitchen-panel-history" role="tabpanel" aria-labelledby="kitchen-tab-history"><section className="desk-box"><div className="desk-box-head"><h2>Registro de decisiones</h2><span>{desk.events.length} eventos más recientes</span></div><div className="desk-log">{desk.events.map(event => <div key={event.id} className={newEvents.has(event.id) ? 'is-new' : ''}><time>{timeOf(event.at)}</time><strong>{event.role}</strong><span>{event.detail}<small className="kitchen-log-status">{statusOf(event.status)}</small></span></div>)}</div></section><section className="desk-box"><div className="desk-box-head"><h2>Operaciones</h2><span>Comisiones y deslizamiento incluidos</span></div>{desk.trades.length === 0 ? <p className="kitchen-empty">Sin operaciones todavía.</p> : <div className="desk-table-scroll"><table><thead><tr><th>Moneda / entrada</th><th>Capital</th><th>Resultado</th><th>Salida</th></tr></thead><tbody>{desk.trades.map(trade => <tr key={trade.entryKey}><td><strong>{trade.symbol}</strong><small className="kitchen-candidate-reason">{new Date(trade.entryTime * 1000).toLocaleString('es-ES')}</small></td><td>{money(trade.notional)}</td><td className={(trade.pnl ?? 0) >= 0 ? 'up' : 'down'}>{trade.status === 'open' ? 'Abierta' : `${money(trade.pnl ?? 0)} ${desk.currency}`}</td><td>{trade.exitReason ?? 'Stop activo'}{trade.exitTime && <small className="kitchen-candidate-reason">{new Date(trade.exitTime * 1000).toLocaleString('es-ES')}</small>}</td></tr>)}</tbody></table></div>}</section></div>}
      <p className="kitchen-wire-note">Wire comprueba que el mercado exacto está listado y tiene una cotización reciente. Las referencias sociales son opcionales y no forman parte de esta comprobación de mercado.</p>
    </div></details>
    <footer className="kitchen-footer"><span><ChefMark /> Hecho a fuego lento. Supervisado paso a paso.</span><p>Simulación sin apalancamiento · Máximo {money(desk.policy?.maxNotionalPct ?? 100 / 6)} % por entrada · Hasta 3 posiciones · Riesgo objetivo {desk.policy?.riskPct ?? 0.5} % por operación y {desk.policy?.totalRiskPct ?? 1.5} % en la mesa · Límite diario 5 %{desk.monitoring?.riskIndependent ? ' · Vigilancia de stops independiente del escaneo' : ''}</p></footer>
  </main>
}

const MarketDesk = memo(DeskPanel)

export default function DesksPanel() {
  const [market, setMarket] = useState<Desk['id']>('crypto')
  return <>
    <nav className="kitchen-desk-tabs" aria-label="Mesas por mercado">
      {([['crypto', '₿', 'Cripto', '24/7'], ['forex', '↔', 'Forex', '24/5'], ['stocks', '▥', 'Acciones', 'Sesión EE. UU.']] as const).map(([id, icon, name, hours]) =>
        <button key={id} aria-pressed={market === id} onClick={() => setMarket(id)}><span aria-hidden="true">{icon}</span><strong>{name}</strong><small>{hours}</small></button>)}
    </nav>
    <MarketDesk key={market} market={market} />
  </>
}
