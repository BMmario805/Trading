import { useEffect, useRef, useState } from 'react'
import {
  createChart,
  BaselineSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import { fetchBotPerformance } from '../api'
import type { BotPerformance as Perf } from '../types'

const REFRESH_MS = 30_000
const UP = '#26a69a'
const DOWN = '#ef5350'

/** Color fijo por bot para el modo "una línea por bot". */
const BOT_COLORS: Record<string, string> = {
  scalper: '#29b6f6',
  swing: '#f0b90b',
  trend: '#ff7043',
  meanrev: '#b388ff',
  orb: '#26c6da',
  squeeze: '#ec407a',
  flow: '#26a69a',
}
const FALLBACK_COLORS = ['#66bb6a', '#ffa726', '#8d6e63', '#78909c']

const botColor = (id: string, i: number) =>
  BOT_COLORS[id] ?? FALLBACK_COLORS[i % FALLBACK_COLORS.length]

const PRESETS = [
  { id: '1d', label: 'Hoy', seconds: 86_400 },
  { id: '7d', label: '7 días', seconds: 7 * 86_400 },
  { id: '30d', label: '30 días', seconds: 30 * 86_400 },
  { id: 'all', label: 'Todo', seconds: 0 },
] as const

type PresetId = (typeof PRESETS)[number]['id'] | 'custom'

const formatPnl = (v: number, currency: string) =>
  `${v >= 0 ? '+' : '-'}${Math.abs(v).toLocaleString('es-ES', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} ${currency}`

/** Epoch (s) del inicio del día local de un <input type="date"> (YYYY-MM-DD). */
const dateToEpoch = (date: string, endOfDay = false) => {
  if (!date) return undefined
  const t = new Date(`${date}T${endOfDay ? '23:59:59' : '00:00:00'}`)
  return Math.floor(t.getTime() / 1000)
}

export default function BotPerformance({ source = 'paper' }: { source?: 'paper' | 'mt5' }) {
  const [bot, setBot] = useState('')
  const [symbol, setSymbol] = useState('')
  const [preset, setPreset] = useState<PresetId>('7d')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [perBot, setPerBot] = useState(false)
  const [perf, setPerf] = useState<Perf | null>(null)
  const [error, setError] = useState(false)
  const fmtPnl = (v: number) => formatPnl(v, perf?.currency ?? (source === 'paper' ? 'USD' : ''))

  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const baselineRef = useRef<ISeriesApi<'Baseline'> | null>(null)
  const linesRef = useRef<ISeriesApi<'Line'>[]>([])

  // crear el gráfico una sola vez
  useEffect(() => {
    const el = containerRef.current!
    const chart = createChart(el, {
      layout: {
        background: { color: 'transparent' },
        textColor: '#9db2ce',
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: 'rgba(120,140,170,0.08)' },
        horzLines: { color: 'rgba(120,140,170,0.08)' },
      },
      timeScale: { timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
      autoSize: true,
    })
    baselineRef.current = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: 0 },
      topLineColor: UP,
      topFillColor1: 'rgba(38,166,154,0.28)',
      topFillColor2: 'rgba(38,166,154,0.02)',
      bottomLineColor: DOWN,
      bottomFillColor1: 'rgba(239,83,80,0.02)',
      bottomFillColor2: 'rgba(239,83,80,0.28)',
      lineWidth: 2,
      priceFormat: { type: 'custom', formatter: (v: number) => fmtPnl(v) },
    })
    chartRef.current = chart
    return () => {
      chart.remove()
      chartRef.current = null
      baselineRef.current = null
      linesRef.current = []
    }
  }, [])

  // cargar datos al cambiar filtros (+ refresco periódico)
  useEffect(() => {
    let cancelled = false
    const load = () => {
      let from: number | undefined
      let to: number | undefined
      if (preset === 'custom') {
        from = dateToEpoch(fromDate)
        to = dateToEpoch(toDate, true)
      } else {
        const p = PRESETS.find((x) => x.id === preset)!
        if (p.seconds) from = Math.floor(Date.now() / 1000) - p.seconds
      }
      fetchBotPerformance({
        bot: bot || undefined,
        symbol: symbol || undefined,
        from,
        to,
        perBot,
        source,
      })
        .then((p) => {
          if (!cancelled) { setPerf(p); setError(false) }
        })
        .catch(() => { if (!cancelled) setError(true) })
    }
    load()
    const id = window.setInterval(load, source === 'mt5' ? 10_000 : REFRESH_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [bot, symbol, preset, fromDate, toDate, perBot, source])

  // pintar la curva total o una línea por bot
  useEffect(() => {
    const chart = chartRef.current
    const baseline = baselineRef.current
    if (!perf || !chart || !baseline) return
    baseline.applyOptions({ priceFormat: { type: 'custom', formatter: (v: number) => fmtPnl(v) } })
    for (const s of linesRef.current) {
      try {
        chart.removeSeries(s)
      } catch {
        /* ya eliminada */
      }
    }
    linesRef.current = []
    if (perBot && perf.series) {
      baseline.setData([])
      perf.series.forEach((serie, i) => {
        const s = chart.addSeries(LineSeries, {
          color: botColor(serie.id, i),
          lineWidth: 2,
          priceLineVisible: false,
          title: serie.name.replace('Bot ', ''),
          priceFormat: { type: 'custom', formatter: (v: number) => fmtPnl(v) },
        })
        s.setData(
          serie.points.map((p) => ({ time: p.ts as UTCTimestamp, value: p.cumPnl })),
        )
        linesRef.current.push(s)
      })
    } else {
      baseline.setData(
        perf.points.map((p) => ({ time: p.ts as UTCTimestamp, value: p.cumPnl })),
      )
    }
    chart.timeScale().fitContent()
  }, [perf, perBot])

  const s = perf?.summary

  return (
    <div className="panel perf-panel">
      <div className="perf-head">
        <h3>📊 {source === 'mt5' ? 'Rendimiento MT5 · cuenta y bots' : 'Rendimiento de simulación local'}</h3>
        <div className="perf-filters">
          <select value={bot} onChange={(e) => setBot(e.target.value)}>
            <option value="">{source === 'mt5' ? 'Cuenta completa' : 'Todos los bots'}</option>
            {(perf?.bots ?? []).map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            <option value="">Todos los mercados</option>
            {(perf?.symbols ?? []).map((sym) => (
              <option key={sym} value={sym}>
                {sym}
              </option>
            ))}
          </select>
          <button
            className={`chart-toggle ${perBot ? 'active' : ''}`}
            title="Dibuja una curva de PnL independiente por bot en lugar de la suma total"
            onClick={() => setPerBot((v) => !v)}
          >
            Línea por bot
          </button>
          <nav className="perf-presets">
            {PRESETS.map((p) => (
              <button
                key={p.id}
                className={preset === p.id ? 'active' : ''}
                onClick={() => setPreset(p.id)}
              >
                {p.label}
              </button>
            ))}
            <button
              className={preset === 'custom' ? 'active' : ''}
              onClick={() => setPreset('custom')}
            >
              Fechas…
            </button>
          </nav>
          {preset === 'custom' && (
            <span className="perf-dates">
              <input
                type="date"
                value={fromDate}
                onChange={(e) => setFromDate(e.target.value)}
              />
              →
              <input
                type="date"
                value={toDate}
                onChange={(e) => setToDate(e.target.value)}
              />
            </span>
          )}
        </div>
      </div>

      {(error || perf?.syncError) && <p role="status">No se ha podido actualizar el rendimiento. Se muestran los últimos datos sincronizados.</p>}
      {s && (
        <div className="bot-stats perf-stats">
          <div>
            <small className="muted">{source === 'mt5' ? 'PnL neto contabilizado' : 'PnL realizado'}</small>
            <strong className={s.pnl >= 0 ? 'up' : 'down'}>{fmtPnl(s.pnl)}</strong>
          </div>
          <div>
            <small className="muted">Posiciones cerradas</small>
            <strong>{s.trades}</strong>
          </div>
          <div>
            <small className="muted">Acierto</small>
            <strong>{s.winRate != null ? `${s.winRate}%` : '—'}</strong>
          </div>
          <div>
            <small className="muted">Mejor</small>
            <strong className="up">{fmtPnl(s.best)}</strong>
          </div>
          <div>
            <small className="muted">Peor</small>
            <strong className="down">{fmtPnl(s.worst)}</strong>
          </div>
        </div>
      )}

      {perBot && perf?.series && perf.series.length > 0 && (
        <div className="perf-legend">
          {perf.series.map((serie, i) => (
            <span key={serie.id} className="perf-legend-item" title={
              `${serie.summary.trades} operaciones · acierto ${serie.summary.winRate ?? '—'}%`
            }>
              <span
                className="perf-legend-dot"
                style={{ background: botColor(serie.id, i) }}
              />
              {serie.name.replace('Bot ', '')}{' '}
              <strong className={serie.summary.pnl >= 0 ? 'up' : 'down'}>
                {fmtPnl(serie.summary.pnl)}
              </strong>
            </span>
          ))}
        </div>
      )}

      <div className="perf-chart">
        <div className="perf-chart-canvas" ref={containerRef} />
        {perf &&
          (perBot ? (perf.series ?? []).length === 0 : perf.points.length === 0) && (
            <p className="muted small perf-empty">
              Sin operaciones cerradas con estos filtros. La curva se dibuja a medida
              que los bots cierran operaciones.
            </p>
          )}
      </div>
      <p className="muted small">
        {source === 'mt5'
          ? 'Historial MT5: resultado, comisiones, swaps y tasas. Incluye cierres parciales y costes de apertura; excluye aportaciones y retiradas. Actualización cada 10 s. El acierto cuenta posiciones completamente cerradas.'
          : 'PnL acumulado de operaciones cerradas · actualización cada 30 s.'}
      </p>
    </div>
  )
}
