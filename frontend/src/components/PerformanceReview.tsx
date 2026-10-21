import { useEffect, useState } from 'react'
import './PerformanceReview.css'

interface Metrics {
  netClosed: number; legacyClosed: number; netPnl: number; legacyPnl: number
  wins: number; winRate: number | null; expectancy: number | null; profitFactor: number | null
  averageWin: number | null; averageLoss: number | null; breakEvenWinRate: number | null
  fees: number; carry: number; sampleReady: boolean
  riskOverruns: number
}
interface Account {
  id: string; source: string; name: string; currency: string; performance: Metrics
  currentPolicy?: Metrics
  opportunities?: { eligible: number; reviewed: number; evaluated: number; signalPassed: number; costPassed: number; bothPassed: number; minimumAtrPct: number; roundTripPct: number; medianAtrPct: number | null }
}
interface Review { updatedAt: number; accounts: Account[]; priorities: { id: string; title: string; detail: string }[]; runtime?: { active: boolean; detail: string }; marketFeed?: { connected: boolean; fresh: number; watched: number; streamQuotes: number; restFallbacks: number } }
const number = (value: number | null | undefined, digits = 2) => value == null ? '—' : value.toLocaleString('es-ES', { minimumFractionDigits: digits, maximumFractionDigits: digits })

export default function PerformanceReview() {
  const [review, setReview] = useState<Review | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let cancelled = false, pending = false
    const refresh = async () => {
      if (pending) return
      pending = true
      try {
        const response = await fetch('/api/performance/review')
        if (!response.ok) throw new Error('unavailable')
        const value: Review = await response.json()
        if (!cancelled) { setReview(value); setFailed(false) }
      } catch { if (!cancelled) setFailed(true) }
      finally { pending = false }
    }
    void refresh()
    const timer = window.setInterval(() => { void refresh() }, 15_000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [])
  const crypto = review?.accounts.find(row => row.source === 'desk' && row.id === 'crypto')
  const opportunity = crypto?.opportunities
  return <section className="profit-review" aria-labelledby="profit-title">
    <div className="profit-heading"><div><span className="kitchen-eyebrow">RESULTADO Y COSTES</span><h2 id="profit-title">Qué limita el beneficio</h2><p>La expectativa neta mide cuánto gana o pierde de media cada operación cerrada.</p></div><span>Simulación · sin ventaja validada</span></div>
    {failed && <p role="status">Lectura pendiente. Los datos conservados pueden estar desactualizados.</p>}
    {!review ? <p>Preparando el diagnóstico económico…</p> : <>
      <div className="profit-cards">
        <article><small>Cripto · media neta por cierre</small><strong className={(crypto?.performance.expectancy ?? 0) < 0 ? 'down' : ''}>{number(crypto?.performance.expectancy)} <em>USDT</em></strong><p>{crypto?.performance.netClosed ?? 0} cierres históricos · {crypto?.currentPolicy?.netClosed ?? 0} de la política nueva. Las muestras no se mezclan para validar cambios.</p></article>
        <article><small>Cripto · fricción estimada de ida y vuelta</small><strong>{number(opportunity?.roundTripPct, 3)} <em>%</em></strong><p>Con los costes supuestos actuales, el ATR debe superar aproximadamente {number(opportunity?.minimumAtrPct, 3)} % para la geometría de stop y objetivo. Tarifas de cuenta sin verificar.</p></article>
        <article><small>Último análisis · mercados con señal</small><strong>{opportunity?.signalPassed ?? 0} <em>/ {opportunity?.evaluated ?? 0}</em></strong><p>{opportunity?.costPassed ?? 0} superan costes; {opportunity?.bothPassed ?? 0} superan señal y tamaño. Revisados {opportunity?.reviewed ?? 0} de {opportunity?.eligible ?? 0} aptos. Cada entrada sigue su control final.</p></article>
      </div>
      {review.accounts.some(account => account.performance.riskOverruns > 0) && <p className="profit-risk-note">Pérdidas superiores a 1,5 veces el riesgo inicial registrado: {review.accounts.filter(account => account.performance.riskOverruns > 0).map(account => `${account.name}: ${account.performance.riskOverruns}`).join(' · ')}. Los saltos de precio y las interrupciones de vigilancia pueden superar el stop previsto.</p>}
      <details><summary>Comparar cuentas y calidad de las ganancias</summary><div className="profit-table"><table><thead><tr><th>Cuenta / divisa</th><th>Cierres netos</th><th>Resultado neto</th><th>Aciertos</th><th>Ganancia media</th><th>Pérdida media</th><th>Expectativa</th><th>Factor de beneficio</th><th>Costes explícitos</th></tr></thead><tbody>{review.accounts.map(account => <tr key={`${account.source}:${account.id}`}><td><strong>{account.name}</strong><small>{account.currency}{account.performance.legacyClosed > 0 ? ` · ${account.performance.legacyClosed} cierres antiguos sin costes completos, separados` : ''}</small></td><td>{account.performance.netClosed}</td><td className={account.performance.netPnl < 0 ? 'down' : ''}>{number(account.performance.netPnl)}</td><td>{number(account.performance.winRate, 1)} %</td><td>{number(account.performance.averageWin)}</td><td>{number(account.performance.averageLoss)}</td><td className={(account.performance.expectancy ?? 0) < 0 ? 'down' : ''}>{number(account.performance.expectancy)}</td><td>{number(account.performance.profitFactor)}</td><td>{number(account.performance.fees + account.performance.carry)}<small>Comisiones y financiación</small></td></tr>)}</tbody></table></div><p>Importes en la divisa de cada cuenta. Los cierres sin costes completos se excluyen de estas métricas. El factor de beneficio divide ganancias netas entre pérdidas netas; un valor menor que uno describe pérdidas en la muestra. MT5 conserva su informe específico.</p></details>
      <div className="profit-priorities">{review.priorities.map(item => <article key={item.id}><strong>{item.title}</strong><p>{item.detail}</p></article>)}</div>
      {review.marketFeed && <p className="profit-runtime"><strong>Cotizaciones continuas: {review.marketFeed.connected ? 'conectadas' : 'respaldo REST'}.</strong> {review.marketFeed.fresh} de {review.marketFeed.watched} mercados con libro reciente · {review.marketFeed.streamQuotes} cotizaciones reutilizadas · {review.marketFeed.restFallbacks} obtenidas por respaldo.</p>}
      {review.runtime && <p className="profit-runtime"><strong>Protección contra suspensión automática: {review.runtime.active ? 'activa' : 'sin activar'}.</strong> {review.runtime.detail}.</p>}
    </>}
  </section>
}
