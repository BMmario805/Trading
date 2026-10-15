import { useEffect, useRef, useState } from 'react'
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  LineStyle,
  type UTCTimestamp,
} from 'lightweight-charts'
import { fetchTradeDetail } from '../api'
import { drawOverlays, type OverlayToggles } from '../chartOverlays'
import { formatPrice } from './Watchlist'
import type { Confirmation, TradeDetail } from '../types'

const UP = '#26a69a'
const DOWN = '#ef5350'

// en el detalle se enseña todo lo que el bot miró al entrar
const ALL_ON: OverlayToggles = {
  ma: true,
  bb: false,
  vwap: false,
  sr: true,
  trend: true,
  fib: true,
  st: true,
  kc: false,
  dc: false,
  orb: true,
  psar: false,
  ichi: false,
}

const fmtTime = (ts: number) =>
  new Date(ts * 1000).toLocaleString('es-ES', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })

const fmtPnl = (v: number, currency = 'USD') =>
  `${v >= 0 ? '+' : '-'}${Math.abs(v).toLocaleString('es-ES', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} ${currency}`

function voteIcon(vote: number) {
  if (vote > 0.15) return <span className="up">▲</span>
  if (vote < -0.15) return <span className="down">▼</span>
  return <span className="flat">►</span>
}

function ConfirmationList({ confirmations }: { confirmations: Confirmation[] }) {
  return (
    <ul className="confirm-list">
      {confirmations.map((c, i) => (
        <li key={i}>
          {voteIcon(c.vote)}
          <div>
            <strong>{c.system}</strong>
            <small className="muted"> · {c.detail}</small>
          </div>
          <span
            className={`confirm-vote ${c.vote > 0.15 ? 'up' : c.vote < -0.15 ? 'down' : 'flat'}`}
          >
            {c.vote === 0 ? '—' : `${c.vote > 0 ? '+' : ''}${c.vote.toFixed(2)}`}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** Mini-gráfico con las velas y los sistemas tal y como estaban al entrar. */
function SnapshotChart({ trade }: { trade: TradeDetail }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const snap = trade.snapshot
    const el = ref.current
    if (!snap || !el) return
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
      autoSize: true,
    })
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
      borderVisible: false,
    })
    candles.setData(
      snap.candles.map((c) => ({ ...c, time: c.time as UTCTimestamp })),
    )

    const clear = drawOverlays(chart, candles, snap.overlays, ALL_ON)

    const isLong = trade.direction === 'long'
    const mark = (price: number, color: string, title: string) =>
      candles.createPriceLine({
        price,
        color,
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: true,
        title,
      })
    mark(trade.entryPrice, '#f0b90b', `Entrada ${isLong ? '▲' : '▼'}`)
    mark(trade.sl, DOWN, 'SL')
    mark(trade.tp, UP, 'TP')
    if (trade.exitPrice != null) mark(trade.exitPrice, '#9db2ce', 'Salida')

    const lastTime = snap.candles[snap.candles.length - 1]?.time
    if (lastTime) {
      createSeriesMarkers(candles, [
        {
          time: lastTime as UTCTimestamp,
          position: isLong ? 'belowBar' : 'aboveBar',
          color: isLong ? UP : DOWN,
          shape: isLong ? 'arrowUp' : 'arrowDown',
          text: isLong ? 'Largo' : 'Corto',
        },
      ])
    }
    chart.timeScale().fitContent()
    return () => {
      clear()
      chart.remove()
    }
  }, [trade])

  if (!trade.snapshot) return null
  return <div className="snapshot-chart" ref={ref} />
}

interface Props {
  botId: string
  tradeId: number
  currency?: string
  onClose: () => void
}

export default function TradeDetailModal({ botId, tradeId, onClose, currency }: Props) {
  const [trade, setTrade] = useState<TradeDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchTradeDetail(botId, tradeId)
      .then((t) => {
        if (!cancelled) setTrade(t)
      })
      .catch(() => {
        if (!cancelled) setError('No se pudo cargar la operación.')
      })
    return () => {
      cancelled = true
    }
  }, [botId, tradeId])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const snap = trade?.snapshot

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <h3>
            {trade ? (
              <>
                <span className={`dir-badge ${trade.direction}`}>
                  {trade.direction === 'long' ? '▲ Largo' : '▼ Corto'}
                </span>{' '}
                {trade.symbol}
                <span className="muted">
                  {' '}
                  · {trade.status === 'open' ? 'abierta' : 'cerrada'} ·{' '}
                  {fmtTime(trade.entryTime)}
                </span>
              </>
            ) : (
              'Operación'
            )}
          </h3>
          <button className="modal-close" onClick={onClose} aria-label="Cerrar">
            ✕
          </button>
        </header>

        <div className="modal-body">
          {error && <p className="muted">{error}</p>}
          {trade && (
            <>
              <div className="trade-summary">
                <div>
                  <small className="muted">Entrada</small>
                  <strong>{formatPrice(trade.entryPrice)}</strong>
                </div>
                {trade.exitPrice != null && (
                  <div>
                    <small className="muted">Salida</small>
                    <strong>{formatPrice(trade.exitPrice)}</strong>
                  </div>
                )}
                <div>
                  <small className="muted">Stop-loss</small>
                  <strong className="down">{formatPrice(trade.sl)}</strong>
                </div>
                <div>
                  <small className="muted">Take-profit</small>
                  <strong className="up">{formatPrice(trade.tp)}</strong>
                </div>
                <div>
                  <small className="muted">Tamaño</small>
                  <strong>
                    {trade.qty.toLocaleString('es-ES', { maximumFractionDigits: 4 })}
                  </strong>
                </div>
                {trade.pnl != null && (
                  <div>
                    <small className="muted">PnL</small>
                    <strong className={trade.pnl >= 0 ? 'up' : 'down'}>
                      {fmtPnl(trade.pnl,currency)}
                    </strong>
                  </div>
                )}
                {snap && (
                  <div>
                    <small className="muted">Señal</small>
                    <strong className={snap.score >= 0 ? 'up' : 'down'}>
                      {snap.score >= 0 ? '+' : ''}
                      {snap.score.toFixed(2)}
                    </strong>
                  </div>
                )}
              </div>

              {trade.exitReason && (
                <p className="small muted">
                  Cierre por: <strong>{trade.exitReason}</strong>
                </p>
              )}

              {snap ? (
                <>
                  <h4 className="detail-title">
                    Gráfico en el momento de la entrada
                    <small className="muted">
                      {' '}
                      · velas {snap.interval} · medias, soportes/resistencias,
                      tendencias y Fibonacci como los vio el bot
                    </small>
                  </h4>
                  <SnapshotChart trade={trade} />
                  <h4 className="detail-title">
                    Confirmaciones de la señal
                    <small className="muted">
                      {' '}
                      · ▲ empuja al alza, ▼ a la baja, ► informativo
                    </small>
                  </h4>
                  <ConfirmationList confirmations={snap.confirmations} />
                </>
              ) : (
                <>
                  <h4 className="detail-title">Motivos de la entrada</h4>
                  <ul className="confirm-list">
                    {trade.reasons.map((r, i) => (
                      <li key={i}>
                        <span className="flat">►</span>
                        <div>{r}</div>
                      </li>
                    ))}
                  </ul>
                  <p className="small muted">
                    Esta operación es anterior a la instantánea de gráficos: solo
                    se guardaron los motivos en texto.
                  </p>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
