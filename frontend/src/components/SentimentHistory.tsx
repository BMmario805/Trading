import { useEffect, useState } from 'react'
import { fetchSentimentHistory } from '../api'
import type { SentimentPoint } from '../types'

const W = 300
const H = 90
const REFRESH_MS = 60_000

export default function SentimentHistory({ symbol }: { symbol: string }) {
  const [points, setPoints] = useState<SentimentPoint[]>([])

  useEffect(() => {
    let cancelled = false
    const load = () =>
      fetchSentimentHistory(symbol)
        .then((p) => {
          if (!cancelled) setPoints(p)
        })
        .catch((e) => console.error('sentiment history', e))
    setPoints([])
    load()
    const id = window.setInterval(load, REFRESH_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [symbol])

  return (
    <div className="panel">
      <h3>Evolución del sentimiento (72 h)</h3>
      {points.length < 2 ? (
        <p className="muted small">
          Aún no hay suficiente histórico: se guarda una medición cada ~5 min
          mientras la aplicación está en marcha.
        </p>
      ) : (
        <Sparkline points={points} />
      )}
    </div>
  )
}

function Sparkline({ points }: { points: SentimentPoint[] }) {
  const t0 = points[0].ts
  const span = Math.max(points[points.length - 1].ts - t0, 1)
  const xy = points.map((p) => {
    const x = ((p.ts - t0) / span) * (W - 8) + 4
    const y = H - 8 - (p.bullishPct / 100) * (H - 16)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  const last = points[points.length - 1]
  const mid = H - 8 - 0.5 * (H - 16)

  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} className="sparkline" role="img">
        <line
          x1="4"
          x2={W - 4}
          y1={mid}
          y2={mid}
          stroke="currentColor"
          strokeDasharray="4 4"
          opacity="0.25"
        />
        <polyline
          points={xy.join(' ')}
          fill="none"
          stroke={last.bullishPct >= 50 ? 'var(--up)' : 'var(--down)'}
          strokeWidth="2"
          strokeLinejoin="round"
        />
      </svg>
      <div className="senti-legend">
        <span className="muted">100% = todos alcistas · línea discontinua = 50%</span>
        <span className={last.bullishPct >= 50 ? 'up' : 'down'}>
          ahora: {last.bullishPct.toFixed(0)}%
        </span>
      </div>
    </>
  )
}
