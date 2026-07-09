import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchSentimentHistory } from '../api'
import type { SentimentPoint } from '../types'

const REFRESH_MS = 60_000
const RANGES = [
  { label: '6 h', hours: 6 },
  { label: '24 h', hours: 24 },
  { label: '48 h', hours: 48 },
  { label: '72 h', hours: 72 },
] as const

/* --------- helpers --------- */

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })
}

function fmtDate(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleDateString('es-ES', { day: '2-digit', month: '2-digit' })
}

function fmtDateTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleString('es-ES', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/* --------- main component --------- */

export default function SentimentHistory({ symbol }: { symbol: string }) {
  const [allPoints, setAllPoints] = useState<SentimentPoint[]>([])
  const [range, setRange] = useState(72)

  useEffect(() => {
    let cancelled = false
    const load = () =>
      fetchSentimentHistory(symbol, 72)
        .then((p) => {
          if (!cancelled) setAllPoints(p)
        })
        .catch((e) => console.error('sentiment history', e))
    setAllPoints([])
    load()
    const id = window.setInterval(load, REFRESH_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [symbol])

  const points = useMemo(() => {
    if (allPoints.length === 0) return allPoints
    const cutoff = allPoints[allPoints.length - 1].ts - range * 3600
    return allPoints.filter((p) => p.ts >= cutoff)
  }, [allPoints, range])

  return (
    <div className="panel senti-hist-panel">
      <div className="senti-hist-head">
        <h3>📊 Evolución del sentimiento</h3>
        <div className="senti-ranges">
          {RANGES.map((r) => (
            <button
              key={r.hours}
              className={range === r.hours ? 'active' : ''}
              onClick={() => setRange(r.hours)}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>
      {points.length < 2 ? (
        <div className="senti-hist-empty">
          <p className="muted">
            Aún no hay suficiente histórico. Se guarda una medición cada ~5 min
            mientras la aplicación está en marcha.
          </p>
        </div>
      ) : (
        <SentimentChart points={points} rangeHours={range} />
      )}
    </div>
  )
}

/* --------- chart --------- */

const PAD = { top: 28, right: 16, bottom: 38, left: 44 }

interface TooltipData {
  x: number
  y: number
  point: SentimentPoint
}

function SentimentChart({
  points,
  rangeHours,
}: {
  points: SentimentPoint[]
  rangeHours: number
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [dims, setDims] = useState({ w: 800, h: 240 })
  const [tooltip, setTooltip] = useState<TooltipData | null>(null)
  const [entered, setEntered] = useState(false)

  // Responsive resize
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      const { width } = entries[0].contentRect
      if (width > 0) setDims({ w: width, h: Math.max(200, Math.min(300, width * 0.3)) })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Animate entry
  useEffect(() => {
    const t = requestAnimationFrame(() => setEntered(true))
    return () => {
      cancelAnimationFrame(t)
      setEntered(false)
    }
  }, [points])

  const { w, h } = dims
  const cw = w - PAD.left - PAD.right
  const ch = h - PAD.top - PAD.bottom

  const t0 = points[0].ts
  const t1 = points[points.length - 1].ts
  const span = Math.max(t1 - t0, 1)

  const maxSample = Math.max(...points.map((p) => p.sample), 1)

  const toX = useCallback((ts: number) => PAD.left + ((ts - t0) / span) * cw, [t0, span, cw])
  const toY = useCallback((pct: number) => PAD.top + (1 - pct / 100) * ch, [ch])
  const sampleY = useCallback(
    (s: number) => PAD.top + ch - (s / maxSample) * ch * 0.25,
    [ch, maxSample],
  )

  // Build polylines
  const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${toX(p.ts).toFixed(1)},${toY(p.bullishPct).toFixed(1)}`).join(' ')

  // Gradient fill area
  const areaPath = `${linePath} L${toX(t1).toFixed(1)},${(PAD.top + ch).toFixed(1)} L${toX(t0).toFixed(1)},${(PAD.top + ch).toFixed(1)} Z`

  // Sample size area
  const samplePath = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${toX(p.ts).toFixed(1)},${sampleY(p.sample).toFixed(1)}`)
    .join(' ')
  const sampleArea = `${samplePath} L${toX(t1).toFixed(1)},${(PAD.top + ch).toFixed(1)} L${toX(t0).toFixed(1)},${(PAD.top + ch).toFixed(1)} Z`

  // Y-axis labels
  const yTicks = [0, 25, 50, 75, 100]

  // X-axis time labels
  const xTickCount = Math.min(Math.floor(cw / 80), 8)
  const xTicks: number[] = []
  for (let i = 0; i <= xTickCount; i++) {
    xTicks.push(t0 + (span * i) / xTickCount)
  }

  // Zones
  const y50 = toY(50)
  const bullZoneH = y50 - PAD.top
  const bearZoneH = PAD.top + ch - y50

  // Stats
  const last = points[points.length - 1]
  const first = points[0]
  const delta = last.bullishPct - first.bullishPct
  const avg = points.reduce((s, p) => s + p.bullishPct, 0) / points.length
  const min = Math.min(...points.map((p) => p.bullishPct))
  const max = Math.max(...points.map((p) => p.bullishPct))

  // Mouse interaction
  const onMouseMove = useCallback(
    (e: React.MouseEvent<SVGSVGElement>) => {
      const svg = e.currentTarget
      const rect = svg.getBoundingClientRect()
      const mx = e.clientX - rect.left
      const scaleX = w / rect.width
      const svgX = mx * scaleX

      // Find closest point
      let closest = 0
      let bestDist = Infinity
      for (let i = 0; i < points.length; i++) {
        const px = toX(points[i].ts)
        const d = Math.abs(px - svgX)
        if (d < bestDist) {
          bestDist = d
          closest = i
        }
      }
      const p = points[closest]
      setTooltip({
        x: toX(p.ts),
        y: toY(p.bullishPct),
        point: p,
      })
    },
    [points, toX, toY, w],
  )

  const sentimentLabel = last.bullishPct >= 60 ? 'Alcista' : last.bullishPct <= 40 ? 'Bajista' : 'Neutral'
  const sentimentColor = last.bullishPct >= 60 ? 'var(--up)' : last.bullishPct <= 40 ? 'var(--down)' : 'var(--muted)'

  return (
    <>
      {/* Stats row */}
      <div className="senti-stats-row">
        <div className="senti-stat">
          <small className="muted">Actual</small>
          <strong style={{ color: sentimentColor }}>
            {last.bullishPct.toFixed(1)}% {sentimentLabel}
          </strong>
        </div>
        <div className="senti-stat">
          <small className="muted">Variación</small>
          <strong className={delta >= 0 ? 'up' : 'down'}>
            {delta >= 0 ? '+' : ''}{delta.toFixed(1)}pp
          </strong>
        </div>
        <div className="senti-stat">
          <small className="muted">Media</small>
          <strong>{avg.toFixed(1)}%</strong>
        </div>
        <div className="senti-stat">
          <small className="muted">Rango</small>
          <strong>{min.toFixed(0)}% – {max.toFixed(0)}%</strong>
        </div>
        <div className="senti-stat">
          <small className="muted">Muestras</small>
          <strong>{last.sample.toLocaleString('es-ES')}</strong>
        </div>
      </div>

      {/* Chart */}
      <div className="senti-chart-wrap" ref={containerRef}>
        <svg
          viewBox={`0 0 ${w} ${h}`}
          className="senti-chart-svg"
          onMouseMove={onMouseMove}
          onMouseLeave={() => setTooltip(null)}
        >
          <defs>
            {/* Bullish gradient */}
            <linearGradient id="senti-grad-bull" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="var(--up)" stopOpacity="0.35" />
              <stop offset="100%" stopColor="var(--up)" stopOpacity="0.02" />
            </linearGradient>
            {/* Bearish gradient */}
            <linearGradient id="senti-grad-bear" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="var(--down)" stopOpacity="0.02" />
              <stop offset="100%" stopColor="var(--down)" stopOpacity="0.2" />
            </linearGradient>
            {/* Sample size gradient */}
            <linearGradient id="senti-grad-sample" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.15" />
              <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.03" />
            </linearGradient>
            {/* Line gradient */}
            <linearGradient id="senti-line-grad" x1="0" x2="1" y1="0" y2="0">
              <stop offset="0%" stopColor="var(--up)" stopOpacity="0.6" />
              <stop offset="50%" stopColor="var(--accent)" />
              <stop offset="100%" stopColor={last.bullishPct >= 50 ? 'var(--up)' : 'var(--down)'} />
            </linearGradient>
            {/* Clip for animation */}
            <clipPath id="senti-clip">
              <rect
                x={PAD.left}
                y={PAD.top}
                width={entered ? cw : 0}
                height={ch}
                style={{ transition: 'width 1s cubic-bezier(0.22, 1, 0.36, 1)' }}
              />
            </clipPath>
          </defs>

          {/* Background zones */}
          <rect
            x={PAD.left}
            y={PAD.top}
            width={cw}
            height={bullZoneH}
            fill="url(#senti-grad-bull)"
          />
          <rect
            x={PAD.left}
            y={y50}
            width={cw}
            height={bearZoneH}
            fill="url(#senti-grad-bear)"
          />

          {/* Grid lines */}
          {yTicks.map((tick) => (
            <g key={tick}>
              <line
                x1={PAD.left}
                x2={PAD.left + cw}
                y1={toY(tick)}
                y2={toY(tick)}
                stroke={tick === 50 ? 'var(--muted)' : 'var(--border)'}
                strokeWidth={tick === 50 ? 1.5 : 0.5}
                strokeDasharray={tick === 50 ? '6 4' : '2 4'}
                opacity={tick === 50 ? 0.6 : 0.5}
              />
              <text
                x={PAD.left - 6}
                y={toY(tick) + 4}
                textAnchor="end"
                className="senti-axis-label"
              >
                {tick}%
              </text>
            </g>
          ))}

          {/* X-axis time ticks */}
          {xTicks.map((ts, i) => (
            <g key={i}>
              <line
                x1={toX(ts)}
                x2={toX(ts)}
                y1={PAD.top}
                y2={PAD.top + ch}
                stroke="var(--border)"
                strokeWidth="0.5"
                strokeDasharray="2 4"
                opacity="0.4"
              />
              <text
                x={toX(ts)}
                y={PAD.top + ch + 14}
                textAnchor="middle"
                className="senti-axis-label"
              >
                {rangeHours <= 24 ? fmtTime(ts) : fmtDate(ts)}
              </text>
              {rangeHours > 24 && (
                <text
                  x={toX(ts)}
                  y={PAD.top + ch + 26}
                  textAnchor="middle"
                  className="senti-axis-label"
                  opacity="0.5"
                >
                  {fmtTime(ts)}
                </text>
              )}
            </g>
          ))}

          {/* Zone labels */}
          <text
            x={PAD.left + 6}
            y={PAD.top + 14}
            className="senti-zone-label up"
          >
            ALCISTA
          </text>
          <text
            x={PAD.left + 6}
            y={PAD.top + ch - 6}
            className="senti-zone-label down"
          >
            BAJISTA
          </text>

          {/* Clipped content for animation */}
          <g clipPath="url(#senti-clip)">
            {/* Sample size area */}
            <path d={sampleArea} fill="url(#senti-grad-sample)" />

            {/* Main sentiment area fill */}
            <path d={areaPath} fill="url(#senti-grad-bull)" opacity="0.4" />

            {/* Main line */}
            <path
              d={linePath}
              fill="none"
              stroke="url(#senti-line-grad)"
              strokeWidth="2.5"
              strokeLinejoin="round"
              strokeLinecap="round"
            />

            {/* Data point dots (show first, last, and extremes) */}
            {points.map((p, i) => {
              const isFirst = i === 0
              const isLast = i === points.length - 1
              const isMin = p.bullishPct === min
              const isMax = p.bullishPct === max
              if (!isFirst && !isLast && !isMin && !isMax) return null
              return (
                <circle
                  key={i}
                  cx={toX(p.ts)}
                  cy={toY(p.bullishPct)}
                  r={isLast ? 4.5 : 3}
                  fill={p.bullishPct >= 50 ? 'var(--up)' : 'var(--down)'}
                  stroke="var(--bg-2)"
                  strokeWidth="2"
                />
              )
            })}
          </g>

          {/* Tooltip crosshair */}
          {tooltip && (
            <g>
              <line
                x1={tooltip.x}
                x2={tooltip.x}
                y1={PAD.top}
                y2={PAD.top + ch}
                stroke="var(--text)"
                strokeWidth="0.8"
                strokeDasharray="3 3"
                opacity="0.4"
              />
              <line
                x1={PAD.left}
                x2={PAD.left + cw}
                y1={tooltip.y}
                y2={tooltip.y}
                stroke="var(--text)"
                strokeWidth="0.8"
                strokeDasharray="3 3"
                opacity="0.3"
              />
              <circle
                cx={tooltip.x}
                cy={tooltip.y}
                r={6}
                fill={tooltip.point.bullishPct >= 50 ? 'var(--up)' : 'var(--down)'}
                stroke="var(--bg-2)"
                strokeWidth="2.5"
              />
              {/* Glow */}
              <circle
                cx={tooltip.x}
                cy={tooltip.y}
                r={10}
                fill="none"
                stroke={tooltip.point.bullishPct >= 50 ? 'var(--up)' : 'var(--down)'}
                strokeWidth="1"
                opacity="0.3"
              />
            </g>
          )}

          {/* Axis borders */}
          <line
            x1={PAD.left}
            x2={PAD.left}
            y1={PAD.top}
            y2={PAD.top + ch}
            stroke="var(--border)"
            strokeWidth="1"
          />
          <line
            x1={PAD.left}
            x2={PAD.left + cw}
            y1={PAD.top + ch}
            y2={PAD.top + ch}
            stroke="var(--border)"
            strokeWidth="1"
          />
        </svg>

        {/* Floating tooltip card */}
        {tooltip && (
          <div
            className="senti-tooltip"
            style={{
              left: `${(tooltip.x / w) * 100}%`,
              top: `${((tooltip.y / h) * 100) - 2}%`,
            }}
          >
            <div className="senti-tooltip-time">{fmtDateTime(tooltip.point.ts)}</div>
            <div
              className="senti-tooltip-value"
              style={{
                color:
                  tooltip.point.bullishPct >= 60
                    ? 'var(--up)'
                    : tooltip.point.bullishPct <= 40
                      ? 'var(--down)'
                      : 'var(--text)',
              }}
            >
              {tooltip.point.bullishPct.toFixed(1)}% alcista
            </div>
            <div className="senti-tooltip-sample">
              {tooltip.point.sample} fuentes
            </div>
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="senti-hist-legend">
        <span>
          <span className="senti-legend-dot" style={{ background: 'var(--up)' }} />
          Sentimiento alcista (%)
        </span>
        <span>
          <span className="senti-legend-dot" style={{ background: 'var(--accent)', opacity: 0.4 }} />
          Tamaño de muestra
        </span>
        <span className="muted">
          Línea discontinua = 50% (neutral)
        </span>
      </div>
    </>
  )
}
