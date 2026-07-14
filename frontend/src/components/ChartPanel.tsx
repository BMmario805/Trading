import { useEffect, useRef, useState } from 'react'
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import { fetchCandles, fetchIndicators, fetchOverlays } from '../api'
import {
  DEFAULT_TOGGLES,
  drawOverlays,
  OVERLAY_OPTIONS,
  type OverlayToggles,
} from '../chartOverlays'
import {
  INTERVAL_SECONDS,
  type Candle,
  type IndicatorPanel,
  type Interval,
  type Overlays,
  type Tick,
} from '../types'
import type { PriceSocket } from '../api'

const UP = '#26a69a'
const DOWN = '#ef5350'
const OVERLAYS_REFRESH_MS = 60_000

/** Osciladores disponibles en el subpanel bajo el gráfico. */
const INDICATOR_OPTIONS = [
  { key: '', label: 'Sin indicador' },
  { key: 'rsi', label: 'RSI (14)' },
  { key: 'macd', label: 'MACD' },
  { key: 'stoch', label: 'Estocástico' },
  { key: 'adx', label: 'ADX' },
  { key: 'mfi', label: 'MFI (flujo de dinero)' },
  { key: 'cci', label: 'CCI' },
  { key: 'obv', label: 'OBV' },
  { key: 'roc', label: 'Momento (ROC)' },
  { key: 'atr', label: 'ATR' },
] as const

type IndicatorKey = (typeof INDICATOR_OPTIONS)[number]['key']

/** Velas Heikin-Ashi calculadas a partir de las velas normales. */
function toHeikinAshi(candles: Candle[]): Candle[] {
  const out: Candle[] = []
  for (let i = 0; i < candles.length; i++) {
    const c = candles[i]
    const haClose = (c.open + c.high + c.low + c.close) / 4
    const haOpen =
      i === 0 ? (c.open + c.close) / 2 : (out[i - 1].open + out[i - 1].close) / 2
    out.push({
      time: c.time,
      open: haOpen,
      high: Math.max(c.high, haOpen, haClose),
      low: Math.min(c.low, haOpen, haClose),
      close: haClose,
      volume: c.volume,
    })
  }
  return out
}

interface Props {
  symbol: string
  interval: Interval
  socket: PriceSocket
}

export default function ChartPanel({ symbol, interval, socket }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const lastBarRef = useRef<Candle | null>(null)
  const clearOverlaysRef = useRef<(() => void) | null>(null)
  const rawRef = useRef<Candle[]>([])
  // velas HA anterior y actual (la transformación es recursiva)
  const haPrevRef = useRef<Candle | null>(null)
  const haLastRef = useRef<Candle | null>(null)
  const indSeriesRef = useRef<ISeriesApi<'Line' | 'Histogram'>[]>([])

  const [overlays, setOverlays] = useState<Overlays | null>(null)
  const [toggles, setToggles] = useState<OverlayToggles>(DEFAULT_TOGGLES)
  const [indicator, setIndicator] = useState<IndicatorKey>('')
  const [heikin, setHeikin] = useState(false)

  // crear el gráfico una sola vez
  useEffect(() => {
    const el = containerRef.current!
    const chart = createChart(el, {
      layout: {
        background: { color: 'transparent' },
        textColor: '#9db2ce',
        attributionLogo: true,
      },
      grid: {
        vertLines: { color: 'rgba(120,140,170,0.08)' },
        horzLines: { color: 'rgba(120,140,170,0.08)' },
      },
      timeScale: { timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
      autoSize: true,
    })
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
      borderVisible: false,
    })
    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: 'vol',
      priceFormat: { type: 'volume' },
      color: 'rgba(120,140,170,0.35)',
    })
    chart.priceScale('vol').applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    })
    chartRef.current = chart
    candleRef.current = candles
    volumeRef.current = volume
    return () => {
      clearOverlaysRef.current = null
      chart.remove()
      chartRef.current = null
    }
  }, [])

  // cargar velas al cambiar símbolo/intervalo (o el modo Heikin-Ashi)
  useEffect(() => {
    let cancelled = false
    lastBarRef.current = null
    fetchCandles(symbol, interval)
      .then((data) => {
        if (cancelled || !candleRef.current) return
        rawRef.current = data
        const shown = heikin ? toHeikinAshi(data) : data
        candleRef.current.setData(
          shown.map((c) => ({ ...c, time: c.time as UTCTimestamp })),
        )
        haLastRef.current = heikin ? shown[shown.length - 1] ?? null : null
        haPrevRef.current = heikin ? shown[shown.length - 2] ?? null : null
        volumeRef.current?.setData(
          data.map((c) => ({
            time: c.time as UTCTimestamp,
            value: c.volume,
            color:
              c.close >= c.open
                ? 'rgba(38,166,154,0.35)'
                : 'rgba(239,83,80,0.35)',
          })),
        )
        lastBarRef.current = data[data.length - 1] ?? null
        chartRef.current?.timeScale().scrollToRealTime()
      })
      .catch((e) => console.error('candles', e))
    return () => {
      cancelled = true
    }
  }, [symbol, interval, heikin])

  // trazados de los sistemas (S/R, tendencias, fib, medias…) del backend
  useEffect(() => {
    let cancelled = false
    setOverlays(null)
    const load = () =>
      fetchOverlays(symbol, interval)
        .then((o) => {
          if (!cancelled) setOverlays(o)
        })
        .catch((e) => console.error('overlays', e))
    load()
    const id = window.setInterval(load, OVERLAYS_REFRESH_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [symbol, interval])

  // (re)dibujar los overlays activos
  useEffect(() => {
    clearOverlaysRef.current?.()
    clearOverlaysRef.current = null
    const chart = chartRef.current
    const candles = candleRef.current
    if (!chart || !candles || !overlays) return
    clearOverlaysRef.current = drawOverlays(chart, candles, overlays, toggles)
    return () => {
      clearOverlaysRef.current?.()
      clearOverlaysRef.current = null
    }
  }, [overlays, toggles])

  // subpanel de indicadores (RSI, MACD, estocástico, ADX…) en el panel 1
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    let cancelled = false
    const clear = () => {
      for (const s of indSeriesRef.current) {
        try {
          chart.removeSeries(s)
        } catch {
          /* el gráfico pudo desmontarse */
        }
      }
      indSeriesRef.current = []
    }
    clear()
    if (!indicator) return

    const toData = (pts: { time: number; value: number }[]) =>
      pts.map((p) => ({ time: p.time as UTCTimestamp, value: p.value }))
    const addLine = (
      pts: { time: number; value: number }[],
      color: string,
    ): ISeriesApi<'Line'> => {
      const s = chart.addSeries(
        LineSeries,
        {
          color,
          lineWidth: 1,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        },
        1,
      )
      s.setData(toData(pts))
      indSeriesRef.current.push(s)
      return s
    }
    const guide = (
      s: ISeriesApi<'Line'>,
      price: number,
      color = 'rgba(120,140,170,0.5)',
    ) =>
      s.createPriceLine({
        price,
        color,
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        axisLabelVisible: false,
        title: '',
      })

    const render = (panel: IndicatorPanel) => {
      if (cancelled || !chartRef.current) return
      clear()
      switch (indicator) {
        case 'macd': {
          const h = chart.addSeries(
            HistogramSeries,
            { priceLineVisible: false, lastValueVisible: false },
            1,
          )
          h.setData(
            panel.macd.hist.map((p) => ({
              time: p.time as UTCTimestamp,
              value: p.value,
              color: p.value >= 0 ? 'rgba(38,166,154,0.5)' : 'rgba(239,83,80,0.5)',
            })),
          )
          indSeriesRef.current.push(h)
          addLine(panel.macd.line, '#29b6f6')
          addLine(panel.macd.signal, '#f0b90b')
          break
        }
        case 'stoch': {
          const k = addLine(panel.stoch.k, '#29b6f6')
          addLine(panel.stoch.d, '#f0b90b')
          guide(k, 80, 'rgba(239,83,80,0.5)')
          guide(k, 20, 'rgba(38,166,154,0.5)')
          break
        }
        case 'rsi': {
          const s = addLine(panel.rsi, '#b388ff')
          guide(s, 70, 'rgba(239,83,80,0.5)')
          guide(s, 30, 'rgba(38,166,154,0.5)')
          break
        }
        case 'mfi': {
          const s = addLine(panel.mfi, '#26c6da')
          guide(s, 80, 'rgba(239,83,80,0.5)')
          guide(s, 20, 'rgba(38,166,154,0.5)')
          break
        }
        case 'cci': {
          const s = addLine(panel.cci, '#ec407a')
          guide(s, 100, 'rgba(239,83,80,0.5)')
          guide(s, -100, 'rgba(38,166,154,0.5)')
          break
        }
        case 'adx': {
          const s = addLine(panel.adx, '#ffa726')
          guide(s, 25)
          break
        }
        case 'obv':
          addLine(panel.obv, '#9ccc65')
          break
        case 'roc': {
          const s = addLine(panel.roc, '#7e57c2')
          guide(s, 0)
          break
        }
        case 'atr':
          addLine(panel.atr, '#8d6e63')
          break
      }
      try {
        chart.panes()[1]?.setHeight(140)
      } catch {
        /* API de paneles no disponible */
      }
    }

    const load = () =>
      fetchIndicators(symbol, interval)
        .then((p) => {
          if (!cancelled) render(p)
        })
        .catch((e) => console.error('indicators', e))
    load()
    const id = window.setInterval(load, OVERLAYS_REFRESH_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
      clear()
    }
  }, [indicator, symbol, interval])

  // ticks en vivo -> actualizar la última vela (normal o Heikin-Ashi)
  useEffect(() => {
    const secs = INTERVAL_SECONDS[interval]
    return socket.onTick((t: Tick) => {
      if (t.symbol !== symbol) return
      const series = candleRef.current
      let bar = lastBarRef.current
      if (!series || !bar) return
      const bucket = Math.floor(t.time / secs) * secs
      const isNew = bucket > bar.time
      if (isNew) {
        bar = {
          time: bucket,
          open: t.price,
          high: t.price,
          low: t.price,
          close: t.price,
          volume: 0,
        }
      } else {
        bar = {
          ...bar,
          close: t.price,
          high: Math.max(bar.high, t.price),
          low: Math.min(bar.low, t.price),
        }
      }
      lastBarRef.current = bar
      if (!heikin) {
        series.update({ ...bar, time: bar.time as UTCTimestamp })
        return
      }
      // HA es recursiva: el open de la vela viene de la HA anterior
      if (isNew && haLastRef.current) haPrevRef.current = haLastRef.current
      const prev = haPrevRef.current
      const haClose = (bar.open + bar.high + bar.low + bar.close) / 4
      const haOpen = prev ? (prev.open + prev.close) / 2 : (bar.open + bar.close) / 2
      const haBar: Candle = {
        time: bar.time,
        open: haOpen,
        high: Math.max(bar.high, haOpen, haClose),
        low: Math.min(bar.low, haOpen, haClose),
        close: haClose,
        volume: bar.volume,
      }
      haLastRef.current = haBar
      series.update({ ...haBar, time: haBar.time as UTCTimestamp })
    })
  }, [symbol, interval, socket, heikin])

  return (
    <div className="chart-wrap">
      <div className="chart-tools">
        <small className="muted">Sistemas:</small>
        {OVERLAY_OPTIONS.map((o) => (
          <button
            key={o.key}
            className={`chart-toggle ${toggles[o.key] ? 'active' : ''}`}
            title={o.title}
            disabled={o.key === 'vwap' && !!overlays && !overlays.vwap}
            onClick={() =>
              setToggles((t) => ({ ...t, [o.key]: !t[o.key] }))
            }
          >
            {o.label}
          </button>
        ))}
        <button
          className={`chart-toggle ${heikin ? 'active' : ''}`}
          title="Velas Heikin-Ashi: promedian el ruido y alargan las rachas de tendencia"
          onClick={() => setHeikin((v) => !v)}
        >
          Heikin-Ashi
        </button>
        <select
          className="chart-indicator"
          value={indicator}
          title="Oscilador en un subpanel bajo el precio"
          onChange={(e) => setIndicator(e.target.value as IndicatorKey)}
        >
          {INDICATOR_OPTIONS.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      <div className="chart" ref={containerRef} />
    </div>
  )
}
