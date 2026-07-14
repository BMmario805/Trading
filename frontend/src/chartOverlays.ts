/** Dibujo de los sistemas de trading sobre un gráfico de lightweight-charts:
 * medias, Bollinger, VWAP, soportes/resistencias, líneas de tendencia y
 * Fibonacci. Compartido por el gráfico principal y el detalle de operación. */
import {
  LineSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { OverlayPoint, Overlays } from './types'

export interface OverlayToggles {
  ma: boolean
  bb: boolean
  vwap: boolean
  sr: boolean
  trend: boolean
  fib: boolean
  st: boolean
  kc: boolean
  dc: boolean
  orb: boolean
  psar: boolean
  ichi: boolean
}

export const DEFAULT_TOGGLES: OverlayToggles = {
  ma: true,
  bb: false,
  vwap: false,
  sr: true,
  trend: true,
  fib: false,
  st: false,
  kc: false,
  dc: false,
  orb: false,
  psar: false,
  ichi: false,
}

export const OVERLAY_OPTIONS: { key: keyof OverlayToggles; label: string; title: string }[] = [
  { key: 'ma', label: 'Medias', title: 'Medias móviles exponenciales 20 y 50' },
  { key: 'bb', label: 'Bollinger', title: 'Bandas de Bollinger (20, 2σ)' },
  { key: 'vwap', label: 'VWAP', title: 'Precio medio ponderado por volumen de la sesión (intradía)' },
  { key: 'sr', label: 'Soportes', title: 'Soportes y resistencias por pivotes (nº de toques = fuerza)' },
  { key: 'trend', label: 'Tendencias', title: 'Líneas de tendencia por los dos últimos pivotes' },
  { key: 'fib', label: 'Fibonacci', title: 'Retrocesos de Fibonacci del último impulso' },
  { key: 'st', label: 'SuperTrend', title: 'SuperTrend (10, ×3): stop dinámico por volatilidad' },
  { key: 'kc', label: 'Keltner', title: 'Canales de Keltner (EMA20 ± 1.5×ATR)' },
  { key: 'dc', label: 'Donchian', title: 'Canal de Donchian 20 (máximo/mínimo previos, reglas Turtle)' },
  { key: 'psar', label: 'PSAR', title: 'Parabolic SAR (Wilder): puntos de stop-and-reverse' },
  { key: 'ichi', label: 'Ichimoku', title: 'Nube de Ichimoku (senkou A y B, desplazadas 26 velas)' },
  { key: 'orb', label: 'Apertura', title: 'Rango de los primeros 15 min de la sesión de NYSE (intradía)' },
]

const COLORS = {
  ema20: '#f0b90b',
  ema50: '#b388ff',
  bb: 'rgba(120, 160, 220, 0.55)',
  vwap: '#29b6f6',
  support: '#26a69a',
  resistance: '#ef5350',
  fib: '#d4a017',
  supertrend: '#ff7043',
  keltner: 'rgba(171, 71, 188, 0.6)',
  donchian: 'rgba(38, 198, 218, 0.6)',
  orb: '#ffd54f',
  psar: '#e0e0e0',
  ichiA: 'rgba(38, 166, 154, 0.7)',
  ichiB: 'rgba(239, 83, 80, 0.7)',
}

const toLine = (pts: OverlayPoint[]) =>
  pts.map((p) => ({ time: p.time as UTCTimestamp, value: p.value }))

/** Dibuja los overlays activos; devuelve la función que los borra. */
export function drawOverlays(
  chart: IChartApi,
  candleSeries: ISeriesApi<'Candlestick'>,
  overlays: Overlays,
  on: OverlayToggles,
): () => void {
  const series: ISeriesApi<'Line'>[] = []
  const priceLines: IPriceLine[] = []

  const addLine = (
    data: OverlayPoint[],
    color: string,
    opts: {
      width?: 1 | 2
      style?: LineStyle
      title?: string
      dots?: boolean // solo puntos (PSAR), sin línea continua
    } = {},
  ) => {
    if (data.length < 2) return
    const s = chart.addSeries(LineSeries, {
      color,
      lineWidth: opts.width ?? 1,
      lineStyle: opts.style ?? LineStyle.Solid,
      lineVisible: !opts.dots,
      pointMarkersVisible: opts.dots ?? false,
      pointMarkersRadius: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      title: opts.title ?? '',
    })
    s.setData(toLine(data))
    series.push(s)
  }

  const addPriceLine = (
    price: number,
    color: string,
    title: string,
    style: LineStyle = LineStyle.Dashed,
  ) => {
    priceLines.push(
      candleSeries.createPriceLine({
        price,
        color,
        lineWidth: 1,
        lineStyle: style,
        axisLabelVisible: false,
        title,
      }),
    )
  }

  if (on.ma) {
    addLine(overlays.ema20, COLORS.ema20, { title: 'EMA20' })
    addLine(overlays.ema50, COLORS.ema50, { title: 'EMA50' })
  }
  if (on.bb) {
    addLine(overlays.bollinger.upper, COLORS.bb)
    addLine(overlays.bollinger.middle, COLORS.bb, { style: LineStyle.Dotted })
    addLine(overlays.bollinger.lower, COLORS.bb)
  }
  if (on.vwap && overlays.vwap) {
    addLine(overlays.vwap, COLORS.vwap, { title: 'VWAP' })
  }
  if (on.sr) {
    for (const lvl of overlays.sr) {
      const col = lvl.kind === 'soporte' ? COLORS.support : COLORS.resistance
      addPriceLine(lvl.price, col, `${lvl.kind === 'soporte' ? 'S' : 'R'}×${lvl.touches}`)
    }
  }
  if (on.trend) {
    for (const tl of overlays.trendlines) {
      if (tl.from.time >= tl.to.time) continue
      addLine(
        [
          { time: tl.from.time, value: tl.from.price },
          { time: tl.to.time, value: tl.to.price },
        ],
        tl.kind === 'soporte' ? COLORS.support : COLORS.resistance,
        { width: 2 },
      )
    }
  }
  if (on.fib && overlays.fibonacci) {
    for (const lvl of overlays.fibonacci.levels) {
      addPriceLine(lvl.price, COLORS.fib, `Fib ${(lvl.ratio * 100).toFixed(1)}%`, LineStyle.Dotted)
    }
  }
  if (on.st && overlays.supertrend) {
    addLine(overlays.supertrend, COLORS.supertrend, { title: 'SuperTrend' })
  }
  if (on.kc && overlays.keltner) {
    addLine(overlays.keltner.upper, COLORS.keltner, { style: LineStyle.Dashed })
    addLine(overlays.keltner.lower, COLORS.keltner, { style: LineStyle.Dashed })
  }
  if (on.dc && overlays.donchian) {
    addLine(overlays.donchian.upper, COLORS.donchian)
    addLine(overlays.donchian.lower, COLORS.donchian)
  }
  if (on.orb && overlays.openingRange) {
    addPriceLine(overlays.openingRange.high, COLORS.orb, 'Apertura ↑', LineStyle.SparseDotted)
    addPriceLine(overlays.openingRange.low, COLORS.orb, 'Apertura ↓', LineStyle.SparseDotted)
  }
  if (on.psar && overlays.psar) {
    addLine(overlays.psar, COLORS.psar, { dots: true, title: 'PSAR' })
  }
  if (on.ichi && overlays.ichimoku) {
    addLine(overlays.ichimoku.spanA, COLORS.ichiA, { title: 'Senkou A' })
    addLine(overlays.ichimoku.spanB, COLORS.ichiB, { title: 'Senkou B' })
  }

  return () => {
    for (const s of series) chart.removeSeries(s)
    for (const l of priceLines) candleSeries.removePriceLine(l)
  }
}
