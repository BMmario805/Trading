import { useEffect, useRef } from 'react'
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import { fetchCandles } from '../api'
import {
  INTERVAL_SECONDS,
  type Candle,
  type Interval,
  type Tick,
} from '../types'
import type { PriceSocket } from '../api'

const UP = '#26a69a'
const DOWN = '#ef5350'

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
      chart.remove()
      chartRef.current = null
    }
  }, [])

  // cargar velas al cambiar símbolo/intervalo
  useEffect(() => {
    let cancelled = false
    lastBarRef.current = null
    fetchCandles(symbol, interval)
      .then((data) => {
        if (cancelled || !candleRef.current) return
        candleRef.current.setData(
          data.map((c) => ({ ...c, time: c.time as UTCTimestamp })),
        )
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
  }, [symbol, interval])

  // ticks en vivo -> actualizar la última vela
  useEffect(() => {
    const secs = INTERVAL_SECONDS[interval]
    return socket.onTick((t: Tick) => {
      if (t.symbol !== symbol) return
      const series = candleRef.current
      let bar = lastBarRef.current
      if (!series || !bar) return
      const bucket = Math.floor(t.time / secs) * secs
      if (bucket > bar.time) {
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
      series.update({ ...bar, time: bar.time as UTCTimestamp })
    })
  }, [symbol, interval, socket])

  return <div className="chart" ref={containerRef} />
}
