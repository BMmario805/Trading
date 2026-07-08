import { CATEGORIES, type SymbolInfo, type Tick } from '../types'

interface Props {
  symbols: SymbolInfo[]
  prices: Record<string, Tick>
  selected: string
  onSelect: (id: string) => void
}

export default function Watchlist({ symbols, prices, selected, onSelect }: Props) {
  const grouped = new Map<string, SymbolInfo[]>()
  for (const s of symbols) {
    if (!grouped.has(s.category)) grouped.set(s.category, [])
    grouped.get(s.category)!.push(s)
  }

  return (
    <aside className="watchlist">
      <h1 className="brand">📈 Mercados</h1>
      {[...grouped.entries()].map(([cat, list]) => (
        <section key={cat}>
          <h2 className="cat-title">{CATEGORIES[cat] ?? cat}</h2>
          {list.map((s) => {
            const p = prices[s.id]
            const up = (p?.changePct ?? 0) >= 0
            return (
              <button
                key={s.id}
                className={`row ${selected === s.id ? 'active' : ''}`}
                onClick={() => onSelect(s.id)}
              >
                <span className="row-label">
                  <strong>{s.id}</strong>
                  <small>{s.label}</small>
                </span>
                <span className={`row-price ${up ? 'up' : 'down'}`}>
                  {p ? (
                    <>
                      <strong>{formatPrice(p.price)}</strong>
                      <small>
                        {up ? '▲' : '▼'} {Math.abs(p.changePct).toFixed(2)}%
                      </small>
                    </>
                  ) : (
                    <small className="muted">…</small>
                  )}
                </span>
              </button>
            )
          })}
        </section>
      ))}
    </aside>
  )
}

export function formatPrice(v: number): string {
  if (v >= 1000) return v.toLocaleString('es-ES', { maximumFractionDigits: 2 })
  if (v >= 1) return v.toFixed(2)
  return v.toPrecision(4)
}
