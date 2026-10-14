import { useEffect, useRef, useState } from 'react'
import { createChart, LineSeries, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts'
import { fetchBotPerformance } from '../api'
import type { BotPerformance as Performance } from '../types'
import BotPerformance from './BotPerformance'

export default function MetaTraderPerformance() {
  const [data, setData] = useState<Performance | null>(null)
  const [error, setError] = useState(false)
  const element = useRef<HTMLDivElement>(null)
  const balance = useRef<ISeriesApi<'Line'> | null>(null)
  const equity = useRef<ISeriesApi<'Line'> | null>(null)
  const fit = useRef<(() => void) | null>(null)
  useEffect(() => {
    let cancelled = false
    const refresh = async () => {
      try {
        const result = await fetchBotPerformance({ source: 'mt5' })
        if (!cancelled) { setData(result); setError(false) }
      } catch { if (!cancelled) setError(true) }
    }
    void refresh()
    const timer = window.setInterval(refresh, 10_000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [])
  useEffect(() => {
    if (!element.current) return
    const chart = createChart(element.current, {
      autoSize: true, layout: { background: { color: 'transparent' }, textColor: '#9db2ce', attributionLogo: false },
      grid: { vertLines: { color: 'rgba(120,140,170,.08)' }, horzLines: { color: 'rgba(120,140,170,.08)' } },
      timeScale: { timeVisible: true },
    })
    balance.current = chart.addSeries(LineSeries, { color: '#29b6f6', title: 'Balance', lineWidth: 2 })
    equity.current = chart.addSeries(LineSeries, { color: '#26a69a', title: 'Patrimonio', lineWidth: 2 })
    fit.current = () => chart.timeScale().fitContent()
    return () => { chart.remove(); balance.current = null; equity.current = null; fit.current = null }
  }, [])
  useEffect(() => {
    const rows = data?.accountHistory ?? []
    balance.current?.setData(rows.map(p => ({ time: p.ts as UTCTimestamp, value: p.balance })))
    equity.current?.setData(rows.map(p => ({ time: p.ts as UTCTimestamp, value: p.equity })))
    const options = { priceFormat: { type: 'custom' as const, formatter: (v: number) => `${v.toLocaleString('es-ES', { maximumFractionDigits: 2 })} ${data?.currency ?? ''}` } }
    balance.current?.applyOptions(options)
    equity.current?.applyOptions(options)
    fit.current?.()
  }, [data])
  const money = (v: number | null | undefined) => v == null ? '—' : `${v.toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${data?.currency ?? ''}`
  return <div className="mt5-performance">
    <h3>Rendimiento general de tu cuenta MT5</h3>
    {(error || data?.syncError) && <p role="status">Sincronización pendiente: se conservan los últimos datos disponibles.</p>}
    {Math.abs(data?.account?.reconciliationDifference ?? 0) > .01 && <p role="status">El historial disponible no reconcilia todo el saldo: diferencia de {money(data?.account?.reconciliationDifference)}. El balance y patrimonio mostrados proceden directamente de MT5.</p>}
    <div className="bot-stats mt5-account-stats">
      <div><small>PnL contabilizado</small><strong>{money(data?.account?.realizedPnl)}</strong></div>
      <div><small>PnL flotante</small><strong>{money(data?.account?.unrealizedPnl)}</strong></div>
      <div><small>Resultado total</small><strong>{money(data?.account?.totalPnl)}</strong></div>
      <div><small>Aportaciones netas y créditos</small><strong>{money(data?.account?.netFunding)}</strong></div>
    </div>
    <div className="perf-chart"><div className="perf-chart-canvas" ref={element} /></div>
    <p className="muted small">Azul: balance · Verde: patrimonio con posiciones abiertas. Se registra desde la activación de esta sincronización; no se reconstruye patrimonio pasado sin datos.
      {data?.updatedAt ? ` Última sincronización: ${new Date(data.updatedAt * 1000).toLocaleTimeString('es-ES')}.` : ' Esperando la primera sincronización.'}
    </p>
    <h3>Resultado por bot en MT5</h3>
    <div className="mt5-bots-grid">{data?.cards?.map(card => <div className="mt5-bot-card" key={card.id}>
      <h4>{card.name}</h4>
      <strong className={card.totalPnl >= 0 ? 'up' : 'down'}>{money(card.totalPnl)}</strong>
      <p className="small">Contabilizado: {money(card.realizedPnl)}<br />Flotante: {money(card.unrealizedPnl)}</p>
      <p className="muted small">{card.openPositions} abiertas · {card.closedTrades} cerradas</p>
    </div>)}</div>
    <p className="muted small">Los bots comparten el saldo de la cuenta. Manual / otros robots y Ajustes de cuenta permiten reconciliar el total sin atribuir resultados ajenos a tus bots.</p>
    <BotPerformance source="mt5" />
  </div>
}
