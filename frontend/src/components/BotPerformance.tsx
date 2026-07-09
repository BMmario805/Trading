import { useEffect, useRef, useState } from 'react'
import {
  createChart,
  BaselineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import { fetchBotPerformance } from '../api'
import type { BotPerformance as Perf } from '../types'

const REFRESH_MS = 30_000
const UP = '#26a69a'
const DOWN = '#ef5350'

const PRESETS = [
  { id: '1d', label: 'Hoy', seconds: 86_400 },
  { id: '7d', label: '7 días', seconds: 7 * 86_400 },
  { id: '30d', label: '30 días', seconds: 30 * 86_400 },
  { id: 'all', label: 'Todo', seconds: 0 },
] as const

type PresetId = (typeof PRESETS)[number]['id'] | 'custom'

const BOT_LABELS: Record<string, string> = {
  scalper: 'Scalper (minutos)',
  swing: 'Swing (horas/días)',
}

const fmtPnl = (v: number) =>
  `${v >= 0 ? '+' : '-'}$${Math.abs(v).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`

/** Epoch (s) del inicio del día local de un <input type="date"> (YYYY-MM-DD). */
const dateToEpoch = (date: string, endOfDay = false) => {
  if (!date) return undefined
  const t = new Date(`${date}T${endOfDay ? '23:59:59' : '00:00:00'}`)
  return Math.floor(t.getTime() / 1000)
}

export default function BotPerformance() {
  const [bot, setBot] = useState('')
  const [symbol, setSymbol] = useState('')
  const [preset, setPreset] = useState<PresetId>('7d')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [perf, setPerf] = useState<Perf | null>(null)

  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Baseline'> | null>(null)

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
    seriesRef.current = chart.addSeries(BaselineSeries, {
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
      fetchBotPerformance({ bot: bot || undefined, symbol: symbol || undefined, from, to })
        .then((p) => {
          if (!cancelled) setPerf(p)
        })
        .catch((e) => console.error('performance', e))
    }
    load()
    const id = window.setInterval(load, REFRESH_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [bot, symbol, preset, fromDate, toDate])

  // pintar la curva
  useEffect(() => {
    if (!perf || !seriesRef.current) return
    seriesRef.current.setData(
      perf.points.map((p) => ({ time: p.ts as UTCTimestamp, value: p.cumPnl })),
    )
    chartRef.current?.timeScale().fitContent()
  }, [perf])

  const s = perf?.summary

  return (
    <div className="panel perf-panel">
      <div className="perf-head">
        <h3>📊 Rendimiento de los bots</h3>
        <div className="perf-filters">
          <select value={bot} onChange={(e) => setBot(e.target.value)}>
            <option value="">Ambos bots</option>
            <option value="scalper">{BOT_LABELS.scalper}</option>
            <option value="swing">{BOT_LABELS.swing}</option>
          </select>
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            <option value="">Todos los mercados</option>
            {(perf?.symbols ?? []).map((sym) => (
              <option key={sym} value={sym}>
                {sym}
              </option>
            ))}
          </select>
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

      {s && (
        <div className="bot-stats perf-stats">
          <div>
            <small className="muted">PnL realizado</small>
            <strong className={s.pnl >= 0 ? 'up' : 'down'}>{fmtPnl(s.pnl)}</strong>
          </div>
          <div>
            <small className="muted">Operaciones</small>
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

      <div className="perf-chart" ref={containerRef}>
        {perf && perf.points.length === 0 && (
          <p className="muted small perf-empty">
            Sin operaciones cerradas con estos filtros. La curva se dibuja a medida
            que los bots cierran operaciones.
          </p>
        )}
      </div>
      <p className="muted small">
        PnL acumulado de operaciones cerradas (un punto por operación) · se
        actualiza cada 30 s.
      </p>
    </div>
  )
}
