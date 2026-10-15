import { useEffect, useState } from 'react'
import MetaTraderPerformance from './MetaTraderPerformance'

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
  riskPct?: number
  probationRiskPct?: number
  maxPositions?: number
  training?: string | null
  diagnostics?: Record<string, string>
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
      <p>{status.server} · Balance: <strong>{money(status.balance)}</strong> · Patrimonio: <strong>{money(status.equity)}</strong></p>
      <p className="muted small">Riesgo base {status.riskPct}% por operación · máximo {status.maxPositions} posiciones en la cuenta. Estas operaciones aparecen en MT5 del móvil.</p>
      {status.probationRiskPct != null && <p className="muted small">Mercados sin validar: a prueba, con riesgo objetivo máximo del {status.probationRiskPct}% por operación.</p>}
      <div className="bot-actions"><button disabled={busy} onClick={control}>{status.enabled ? 'Pausar entradas en MT5' : 'Activar entradas demo'}</button></div>
      <p className="muted small">Al pausar entradas se siguen gestionando los stops de las posiciones de los bots. El paper trading de abajo es independiente.</p>
      {status.training && <p>Autoentrenamiento con datos MT5: {status.training}</p>}
      {status.positions.length ? <div className="bot-table-wrap"><table className="bot-table">
        <thead><tr><th>Bot</th><th>Mercado</th><th>Dirección</th><th>Lotes</th><th>SL</th><th>TP</th><th>PnL</th></tr></thead>
        <tbody>{status.positions.map(p => <tr key={p.ticket}><td>{p.bot}</td><td>{p.symbol}</td><td>{p.direction === 'long' ? 'Compra' : 'Venta'}</td><td>{p.volume}</td><td>{p.sl}</td><td>{p.tp}</td><td>{money(p.pnl)}</td></tr>)}</tbody>
      </table></div> : <p>Sin posiciones abiertas en esta cuenta demo.</p>}
      <details><summary>Diagnóstico por bot y mercado</summary><ul>{Object.entries(status.diagnostics ?? {}).map(([key, reason]) => <li key={key}>{key}: {reason}</li>)}</ul></details>
    </>}
    {status?.configured && <MetaTraderPerformance />}
  </section>
}
