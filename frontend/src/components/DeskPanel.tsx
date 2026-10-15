import { useEffect, useRef, useState } from 'react'
import { CandlestickSeries, ColorType, createChart, type IChartApi, type ISeriesApi, type Time } from 'lightweight-charts'

interface Candle { time: number; open: number; high: number; low: number; close: number }
interface Role { name: string; status: string; detail: string; provider: string }
interface Candidate { symbol: string; eligible: boolean; selected: boolean; change24h: number; volumeUSD: number; spreadBp: number; reason: string }
interface Trade { entryKey: string; symbol: string; entry: number; entryTime: number; status: string; notional: number; pnl?: number; exitReason?: string; exitTime?: number }
interface Desk {
  enabled: boolean; equity: number; initial: number; cash: number; benchmark: number
  selected: string | null; benchmarkSymbol: string | null; examined: number; discarded: number; roles: Role[]
  candidates: Candidate[]; candles: Candle[]; trades: Trade[]
  position: null | { symbol: string; notional: number; risk: number; sl: number; tp: number; lastBid: number }
  stats: { closed: number; wins: number; pnl: number }
  events: { id: number; at: number; role: string; status: string; detail: string }[]
  curve: { time: number; equity: number; benchmark: number }[]
  error: string | null; updatedAt: number | null
  gemini: null | { model: string; estimatedCostMonthUSD: number; monthlyCapUSD: number; remaining24h: number }
}

const ICONS: Record<string, string> = { Selector: '◎', Tape: '▥', Price: '⌁', Wire: '⌘', Size: '∑', Exec: '↗', Risk: '◇' }
const PURPOSES: Record<string, string> = { Selector: 'Elige una moneda o efectivo', Tape: 'Lee las velas cerradas', Price: 'Espera el retroceso', Wire: 'Coteja el enlace oficial de X', Size: 'Limita capital y pérdida', Exec: 'Registra la orden simulada', Risk: 'Gestiona stops y bloqueos' }
const STATES: Record<string, string> = { waiting: 'En espera', selected: 'Seleccionado', ready: 'Preparado', blocked: 'Bloqueado', corroborated: 'Referencia corroborada', managing: 'Gestionando', executed: 'Ejecutado', closed: 'Cerrado' }
const money = (value: number) => value.toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

function MarketChart({ candles }: { candles: Candle[] }) {
  const element = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)
  const series = useRef<ISeriesApi<'Candlestick'> | null>(null)
  useEffect(() => {
    if (!element.current) return
    chart.current = createChart(element.current, { autoSize: true, height: 320,
      layout: { background: { type: ColorType.Solid, color: '#101019' }, textColor: '#8e90a7' },
      grid: { vertLines: { color: '#202030' }, horzLines: { color: '#202030' } },
      timeScale: { timeVisible: true }, rightPriceScale: { borderColor: '#303044' } })
    series.current = chart.current.addSeries(CandlestickSeries, { upColor: '#49dfb1', downColor: '#f16c93', borderVisible: false, wickUpColor: '#49dfb1', wickDownColor: '#f16c93' })
    return () => { chart.current?.remove(); chart.current = null; series.current = null }
  }, [])
  useEffect(() => {
    series.current?.setData(candles.map(c => ({ ...c, time: c.time as Time })))
    chart.current?.timeScale().fitContent()
  }, [candles])
  return <div className="desk-candles" ref={element} />
}

function EquityChart({ rows }: { rows: Desk['curve'] }) {
  if (rows.length < 2) return <div className="desk-empty-curve">La curva empieza a registrarse ahora. Sin resultados inventados.</div>
  const values = rows.flatMap(r => [r.equity, r.benchmark])
  const lo = Math.min(...values) - 1, hi = Math.max(...values) + 1
  const points = (key: 'equity' | 'benchmark') => rows.map((r, i) => `${20 + i / (rows.length - 1) * 960},${140 - (r[key] - lo) / (hi - lo) * 115}`).join(' ')
  return <svg viewBox="0 0 1000 165" role="img" aria-label="Patrimonio de la mesa y referencia comprar y mantener">
    {[25, 65, 105, 145].map(y => <line key={y} x1="20" x2="980" y1={y} y2={y} stroke="#262637" />)}
    <polyline points={points('benchmark')} fill="none" stroke="#7888bc" strokeWidth="2" strokeDasharray="5 5" />
    <polyline points={points('equity')} fill="none" stroke="#49dfb1" strokeWidth="2.5" />
  </svg>
}

export default function DeskPanel() {
  const [desk, setDesk] = useState<Desk | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [openRole, setOpenRole] = useState('Selector')
  useEffect(() => {
    let cancelled = false, pending = false
    const load = async () => {
      if (pending) return
      pending = true
      try {
        const response = await fetch('/api/desk')
        if (!response.ok) throw new Error('unavailable')
        const value: Desk = await response.json()
        if (!cancelled) { setDesk(value); setError(null) }
      } catch { if (!cancelled) setError('No se pudo actualizar la mesa. Se conservan los últimos datos.') }
      finally { pending = false }
    }
    void load()
    const timer = window.setInterval(() => { void load() }, 5_000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [])
  const action = async (name: string) => {
    setBusy(true)
    try {
      const response = await fetch(`/api/desk/${name}`, { method: 'POST' })
      if (!response.ok) throw new Error('failed')
      setDesk(await response.json())
    } catch { setError('No se pudo aplicar el control. Comprueba el estado antes de repetirlo.') }
    finally { setBusy(false) }
  }
  if (!desk) return <main className="desk-main"><p role="status">{error ?? 'Conectando la mesa…'}</p></main>
  const activeRole = desk.roles?.find(role => role.name === openRole)
  const net = desk.equity - desk.initial
  return <main className="desk-main">
    <header className="desk-header">
      <div><span className="desk-eyebrow">TRADING / AUTONOMOUS DESK</span><h1>MESA <span>SIETE</span><small>GEMINI + CONTROLES DETERMINISTAS</small></h1></div>
      <div className="desk-controls"><span className={`desk-live ${desk.enabled ? 'on' : ''}`}>{desk.enabled ? '● ACTIVA' : '● ENTRADAS PAUSADAS'}</span>
        <button disabled={busy} onClick={() => { void action(desk.enabled ? 'pause' : 'start') }}>{desk.enabled ? 'Pausar entradas' : 'Activar entradas'}</button>
        <button className="desk-stop" disabled={busy || !desk.position} onClick={() => { void action('close') }}>Cerrar posición y pausar</button>
      </div>
    </header>
    <p className="desk-mode">CUENTA SIMULADA · EXPERIMENTO SIN VENTAJA DEMOSTRADA · NINGUNA ORDEN REAL</p>
    {(error || desk.error) && <p className="error" role="status">{error || desk.error}</p>}
    <section className="desk-metrics">
      <div><small>PATRIMONIO</small><strong>{money(desk.equity)} <em>USDT</em></strong><span className={net >= 0 ? 'up' : 'down'}>{net >= 0 ? '+' : ''}{money(net)} · {(net / desk.initial * 100).toFixed(2)}%</span></div>
      <div><small>MERCADOS EXAMINADOS</small><strong>{desk.examined}<em> / hasta 81</em></strong><span>{desk.selected ? `${desk.discarded} no seleccionados` : 'Selección pendiente; efectivo'}</span></div>
      <div><small>OPERACIONES CERRADAS</small><strong>{desk.stats.closed}</strong><span>{desk.stats.wins} ganadoras · PnL neto {money(desk.stats.pnl)}</span></div>
      <div><small>IA · COSTE ESTIMADO DEL MES</small><strong>{money(desk.gemini?.estimatedCostMonthUSD ?? 0)} <em>USD</em></strong><span>Piloto compartido · tope {desk.gemini?.monthlyCapUSD ?? 10} USD estimados</span></div>
    </section>
    <section className="desk-box desk-equity"><div className="desk-box-head"><h2>Rendimiento · últimas 24 horas</h2><span><i className="desk-legend-strategy" /> Mesa <i className="desk-legend-hold" /> Comprar y mantener {desk.benchmarkSymbol ?? ''}</span></div>
      <EquityChart rows={desk.curve} />
      <p className="muted small">Mismo capital inicial y comisiones. Referencia con 100% invertido desde la selección; exposición distinta de la mesa.</p>
    </section>
    <div className="desk-market-row">
      <section className="desk-box"><div className="desk-box-head"><h2>{desk.selected ?? 'ESPERANDO SELECCIÓN'} <span className="desk-caption">/ SPOT · 15 MIN</span></h2><span>Velas cerradas</span></div>
        <MarketChart candles={desk.candles} />
        {desk.position ? <div className="desk-position"><span>Entrada en {desk.position.symbol}</span><span>Capital: {money(desk.position.notional)} USDT</span><span>Stop: {desk.position.sl.toPrecision(6)}</span><span>Objetivo: {desk.position.tp.toPrecision(6)}</span></div> : <p className="muted small">En efectivo. La mesa espera que sus controles permitan una entrada.</p>}
      </section>
      <section className="desk-box desk-universe"><div className="desk-box-head"><h2>Universo del turno</h2><span>Binance Spot</span></div>
        <div className="desk-table-scroll"><table><thead><tr><th>Moneda</th><th>24 h</th><th>Volumen</th><th>Estado</th></tr></thead><tbody>{desk.candidates.map(c => <tr key={c.symbol} className={c.selected ? 'desk-selected-row' : ''} title={c.reason}>
          <td>{c.symbol.replace(/USDT$/, '')}</td><td className={c.change24h >= 0 ? 'up' : 'down'}>{c.change24h > 0 ? '+' : ''}{c.change24h.toFixed(2)}%</td><td>{(c.volumeUSD / 1e6).toFixed(1)} M</td><td>{c.selected ? 'Elegida' : c.eligible ? 'No elegida' : 'Filtrada'}</td>
        </tr>)}</tbody></table></div>
      </section>
    </div>
    <section className="desk-box desk-team"><div className="desk-box-head"><h2>Una función por puesto</h2><span>Selector → Tape → Price → Wire → Size → Exec → Risk</span></div>
      <div className="desk-selector-row"><button className={`desk-agent desk-selector ${openRole === 'Selector' ? 'chosen' : ''}`} onClick={() => setOpenRole('Selector')}><span className="desk-agent-icon">◎</span><strong>SELECTOR</strong><small>Gemini · elige la moneda o efectivo</small><span>{STATES[desk.roles[0]?.status] ?? 'En espera'}</span></button></div>
      <div className="desk-agent-grid">{desk.roles.filter(role => role.name !== 'Selector').map(role => <button key={role.name} className={`desk-agent ${role.status === 'blocked' ? 'blocked' : ''} ${openRole === role.name ? 'chosen' : ''}`} onClick={() => setOpenRole(role.name)}>
        <span className="desk-agent-icon">{ICONS[role.name]}</span><strong>{role.name.toUpperCase()}</strong><small>{PURPOSES[role.name]}</small><span>{STATES[role.status] ?? role.status}</span>
      </button>)}</div>
      {activeRole && <div className="desk-role-detail"><strong>{activeRole.name} · {activeRole.provider}</strong><p>{activeRole.detail}</p></div>}
      <p className="muted small">Wire coteja un enlace publicado en el sitio oficial. No certifica la propiedad de la cuenta ni la seguridad del token.</p>
    </section>
    <div className="desk-bottom-row">
      <section className="desk-box"><div className="desk-box-head"><h2>Registro de decisiones</h2><span>Eventos reales del motor</span></div><div className="desk-log">{desk.events.map(event => <div key={event.id}><time>{new Date(event.at * 1000).toLocaleTimeString('es-ES')}</time><strong>{event.role}</strong><span>{event.detail}</span></div>)}</div></section>
      <section className="desk-box"><div className="desk-box-head"><h2>Operaciones</h2><span>Comisiones y deslizamiento incluidos</span></div>{desk.trades.length === 0 ? <p className="desk-empty">Sin operaciones todavía.</p> : <div className="desk-table-scroll"><table><thead><tr><th>Moneda</th><th>Capital</th><th>Resultado</th><th>Salida</th></tr></thead><tbody>{desk.trades.map(trade => <tr key={trade.entryKey}><td>{trade.symbol}</td><td>{money(trade.notional)}</td><td className={(trade.pnl ?? 0) >= 0 ? 'up' : 'down'}>{trade.status === 'open' ? 'Abierta' : money(trade.pnl ?? 0)}</td><td>{trade.exitReason ?? 'Stop activo'}</td></tr>)}</tbody></table></div>}</section>
    </div>
    <p className="desk-footer">SIMULACIÓN SPOT SIN APALANCAMIENTO · 1/3 MÁXIMO DE CAPITAL · 1% DE RIESGO OBJETIVO POR OPERACIÓN · 5% DE LÍMITE DIARIO</p>
  </main>
}
