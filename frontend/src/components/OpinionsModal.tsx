import { useEffect, useState } from 'react'
import { fetchOpinions } from '../api'
import type { OpinionBadge, OpinionsResponse } from '../types'

const BADGE_LABEL: Record<OpinionBadge, { icon: string; label: string; cls: string }> = {
  long: { icon: '▲', label: 'Largo', cls: 'up' },
  bullish: { icon: '▲', label: 'Alcista', cls: 'up' },
  positive: { icon: '▲', label: 'Positivo', cls: 'up' },
  short: { icon: '▼', label: 'Corto', cls: 'down' },
  bearish: { icon: '▼', label: 'Bajista', cls: 'down' },
  negative: { icon: '▼', label: 'Negativo', cls: 'down' },
  neutral: { icon: '►', label: 'Neutral', cls: 'flat' },
}

function timeAgo(epoch: number | null): string {
  if (!epoch) return ''
  const s = Date.now() / 1000 - epoch
  if (s < 3600) return `hace ${Math.max(1, Math.round(s / 60))} min`
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`
  return `hace ${Math.round(s / 86400)} d`
}

interface Props {
  symbol: string
  initialSource: string
  onClose: () => void
}

export default function OpinionsModal({ symbol, initialSource, onClose }: Props) {
  const [data, setData] = useState<OpinionsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [active, setActive] = useState(initialSource)

  useEffect(() => {
    let cancelled = false
    fetchOpinions(symbol)
      .then((d) => {
        if (!cancelled) setData(d)
      })
      .catch(() => {
        if (!cancelled) setError('No se pudieron cargar las opiniones.')
      })
    return () => {
      cancelled = true
    }
  }, [symbol])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const current =
    data?.sources.find((s) => s.source === active) ?? data?.sources[0]

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <h3>Opiniones · {symbol}</h3>
          <button className="modal-close" onClick={onClose} aria-label="Cerrar">
            ✕
          </button>
        </header>

        {data && (
          <nav className="modal-tabs">
            {data.sources.map((s) => (
              <button
                key={s.source}
                className={s.source === current?.source ? 'active' : ''}
                onClick={() => setActive(s.source)}
              >
                {s.source} ({s.items.length})
              </button>
            ))}
          </nav>
        )}

        <div className="modal-body">
          {error && <p className="muted">{error}</p>}
          {!data && !error && <p className="muted">Cargando opiniones…</p>}
          {current?.items.map((it, i) => {
            const b = BADGE_LABEL[it.badge] ?? BADGE_LABEL.neutral
            return (
              <article className="opinion" key={i}>
                <header className="op-head">
                  {it.avatar ? (
                    <img className="op-avatar" src={it.avatar} alt="" />
                  ) : (
                    <span className="op-avatar op-avatar-ph">
                      {(it.user ?? '?').slice(0, 1).toUpperCase()}
                    </span>
                  )}
                  <span className="op-user">{it.user ?? 'anónimo'}</span>
                  <span className={`op-badge ${b.cls}`}>
                    {b.icon} {b.label}
                  </span>
                  <span className="op-meta muted">
                    {it.likes !== null && <>👍 {it.likes} · </>}
                    {timeAgo(it.time)}
                  </span>
                </header>
                {it.text && <p className="op-text">{it.text}</p>}
                {it.image && (
                  <a href={it.url ?? it.image} target="_blank" rel="noreferrer">
                    <img
                      className="op-img"
                      src={it.image}
                      alt="Gráfico publicado por el usuario"
                    />
                  </a>
                )}
                {it.url && (
                  <a className="op-link" href={it.url} target="_blank" rel="noreferrer">
                    Ver original ↗
                  </a>
                )}
              </article>
            )
          })}
        </div>
      </div>
    </div>
  )
}
