import { lazy, Suspense, useEffect, useState } from 'react'

const MetaTraderPerformance = lazy(() => import('./MetaTraderPerformance'))

interface DemoStatus {
  configured: boolean
  connected: boolean
  message: string
  enabled?: boolean
  tradingAllowed?: boolean
  server?: string
  currency?: string
  balance?: number
  equity?: number
  brokerBalance?: number
  brokerEquity?: number
  session?: { initial: number; allocatedCapital?: number; startedAt: number } | null
  riskPct?: number
  probationRiskPct?: number
  maxPositions?: number
  training?: string | null
  diagnostics?: Record<string, string>
  riskDiagnostics?: Record<string, { symbol: string; riskBudget: number; minimumLot: number; minimumLotRisk: number; requiredCapital: number }>
  scan?: { completedAt: number; reviewed: number; accepted: number; blockers: { reason: string; count: number }[] }
  positions: { ticket: number; symbol: string; bot: string; direction: string; volume: number; entry: number; sl: number; tp: number; pnl: number }[]
}

export default function MetaTraderPanel() {
  const [status, setStatus] = useState<DemoStatus | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let cancelled = false
    const refresh = async () => {
      try {
        const response = await fetch('/api/mt5')
        if (!response.ok) throw new Error('No se puede consultar MetaTrader')
        const data = await response.json()
        if (!cancelled) { setStatus(data); setError('') }
      } catch {
        if (!cancelled) setError('No se puede actualizar la conexión MT5.')
      }
    }
    void refresh()
    const timer = window.setInterval(refresh, 5000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [])
  if (!status?.configured && !error) return null
  const control = async () => {
    setBusy(true)
    try {
      const response = await fetch(`/api/mt5/${status?.enabled ? 'stop' : 'start'}`, { method: 'POST' })
      if (!response.ok) throw new Error('No se pudo cambiar el estado')
      const data = await response.json()
      setStatus(previous => previous ? { ...previous, enabled: data.enabled } : previous)
    } catch { setError('No se pudo cambiar el estado de MT5.') }
    finally { setBusy(false) }
  }
  const money = (v?: number) => v == null ? '—' : `${v.toLocaleString('es-ES', { maximumFractionDigits: 2 })} ${status?.currency ?? ''}`
  return <section className="panel mt5-panel">
    <h3>MetaTrader 5 · Cuenta DEMO compartida</h3>
    <p role="status" className={status?.tradingAllowed ? 'up' : 'muted'}>{error || status?.message}</p>
    {status?.connected && <>
      <p>{status.server} · {status.session ? 'Presupuesto virtual' : 'Balance'}: <strong>{money(status.balance)}</strong> · Patrimonio: <strong>{money(status.equity)}</strong></p>
      {status.session && <p className="muted small">Capital virtual aportado: {money(status.session.allocatedCapital ?? status.session.initial)}. Las aportaciones se registran por separado del beneficio y se conserva el historial. Saldo de la demo en el bróker: {money(status.brokerBalance)}.</p>}
      <p className="muted small">Riesgo base {status.riskPct}% por operación · máximo {status.maxPositions} posiciones en la cuenta. Estas operaciones aparecen en MT5 del móvil.</p>
      {status.probationRiskPct != null && <p className="muted small">Mercados sin validar: a prueba, con riesgo objetivo máximo del {status.probationRiskPct}% por operación.</p>}
      {status.probationRiskPct != null && status.equity != null && <p className="execution-status">Presupuesto máximo a prueba por entrada: <strong>{money(status.equity * status.probationRiskPct / 100)}</strong>. Cada lote debe caber en ese riesgo; activar los bots no fuerza una operación.</p>}
      {status.scan && <div className="execution-status" role="status"><strong>Última revisión MT5: {new Date(status.scan.completedAt * 1000).toLocaleTimeString('es-ES')}</strong><p>{status.scan.reviewed} combinaciones revisadas · {status.scan.accepted} órdenes aceptadas en esta revisión.</p>{status.scan.blockers.length > 0 && <ul>{status.scan.blockers.slice(0, 4).map(blocker => <li key={blocker.reason}>{blocker.reason}: {blocker.count}</li>)}</ul>}</div>}
      {Object.keys(status.riskDiagnostics ?? {}).length > 0 && <div className="bot-table-wrap"><table className="bot-table"><caption>Viabilidad del lote mínimo en la última revisión</caption><thead><tr><th>Bot / mercado</th><th>Lote mínimo</th><th>Riesgo del lote</th><th>Riesgo permitido</th></tr></thead><tbody>{Object.entries(status.riskDiagnostics ?? {}).map(([key, detail]) => <tr key={key}><td>{key}</td><td>{detail.minimumLot}</td><td>{money(detail.minimumLotRisk)}</td><td>{money(detail.riskBudget)}</td></tr>)}</tbody></table></div>}
      <div className="bot-actions"><button disabled={busy} onClick={control}>{status.enabled ? 'Pausar entradas en MT5' : 'Activar entradas demo'}</button></div>
      <p className="muted small">Al pausar entradas se siguen gestionando los stops de las posiciones de los bots. El paper trading de abajo es independiente.</p>
      {status.training && <p>Autoentrenamiento con datos MT5: {status.training}</p>}
      {status.positions.length ? <div className="bot-table-wrap"><table className="bot-table">
        <thead><tr><th>Bot</th><th>Mercado</th><th>Dirección</th><th>Lotes</th><th>SL</th><th>TP</th><th>PnL</th></tr></thead>
        <tbody>{status.positions.map(p => <tr key={p.ticket}><td>{p.bot}</td><td>{p.symbol}</td><td>{p.direction === 'long' ? 'Compra' : 'Venta'}</td><td>{p.volume}</td><td>{p.sl}</td><td>{p.tp}</td><td>{money(p.pnl)}</td></tr>)}</tbody>
      </table></div> : <p>Sin posiciones abiertas en esta cuenta demo.</p>}
      <details><summary>Diagnóstico por bot y mercado</summary><ul>{Object.entries(status.diagnostics ?? {}).map(([key, reason]) => <li key={key}>{key}: {reason}</li>)}</ul></details>
    </>}
    {status?.configured && <Suspense fallback={<p>Preparando el historial de MT5…</p>}><MetaTraderPerformance /></Suspense>}
  </section>
}
