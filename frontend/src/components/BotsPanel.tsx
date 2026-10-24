import { useCallback, useEffect, useState } from 'react'
import {
  botAction,
  fetchBots,
  fetchBotTrades,
  fetchTraining,
  startTraining,
} from '../api'
import { formatPrice } from './Watchlist'
import BotPerformance from './BotPerformance'
import TradeDetailModal from './TradeDetailModal'
import type { BotLearning, BotStatus, BotTrade, TrainingStatus } from '../types'

const REFRESH_MS = 5_000

const fmtMoney = (v: number, currency = 'USD') =>
  `${v < 0 ? '-' : ''}${Math.abs(v).toLocaleString('es-ES', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} ${currency}`

const fmtPnl = (v: number, currency = 'USD') => (v >= 0 ? `+${fmtMoney(v,currency)}` : fmtMoney(v,currency))

const fmtTime = (ts: number) =>
  new Date(ts * 1000).toLocaleString('es-ES', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })

function PositionsTable({
  bot,
  onSelect,
}: {
  bot: BotStatus
  onSelect: (tradeId: number) => void
}) {
  if (bot.openPositions.length === 0)
    return <p className="muted small">Sin posiciones abiertas ahora mismo.</p>
  return (
    <div className="bot-table-wrap">
      <table className="bot-table">
        <thead>
          <tr>
            <th>Símbolo</th>
            <th>Dir.</th>
            <th>Tamaño</th>
            <th>Entrada</th>
            <th>Actual</th>
            <th>SL</th>
            <th>TP</th>
            <th>PnL</th>
          </tr>
        </thead>
        <tbody>
          {bot.openPositions.map((p) => (
            <tr
              key={p.id}
              className="clickable"
              title="Click para ver las confirmaciones y el gráfico de la entrada"
              onClick={() => onSelect(p.id)}
            >
              <td>{p.symbol}</td>
              <td>
                <span className={`dir-badge ${p.direction}`}>
                  {p.direction === 'long' ? '▲ Largo' : '▼ Corto'}
                </span>
              </td>
              <td>{p.qty.toLocaleString('es-ES', { maximumFractionDigits: 4 })}</td>
              <td>{formatPrice(p.entryPrice)}</td>
              <td>{formatPrice(p.lastPrice)}</td>
              <td className="down">{formatPrice(p.sl)}</td>
              <td className="up">{formatPrice(p.tp)}</td>
              <td className={p.unrealizedPnl >= 0 ? 'up' : 'down'}>
                {fmtPnl(p.unrealizedPnl,bot.currency)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TradesTable({
  trades,
  onSelect,
  currency,
}: {
  trades: BotTrade[]
  onSelect: (tradeId: number) => void
  currency?: string
}) {
  if (trades.length === 0)
    return <p className="muted small">Todavía no hay operaciones cerradas.</p>
  return (
    <div className="bot-table-wrap">
      <table className="bot-table">
        <thead>
          <tr>
            <th>Cierre</th>
            <th>Símbolo</th>
            <th>Dir.</th>
            <th>Entrada → Salida</th>
            <th>Motivo</th>
            <th>PnL</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr
              key={t.id}
              className="clickable"
              title="Click para ver las confirmaciones y el gráfico de la entrada"
              onClick={() => onSelect(t.id)}
            >
              <td>{fmtTime(t.exitTime)}</td>
              <td>{t.symbol}</td>
              <td>
                <span className={`dir-badge ${t.direction}`}>
                  {t.direction === 'long' ? '▲ Largo' : '▼ Corto'}
                </span>
              </td>
              <td>
                {formatPrice(t.entryPrice)} → {formatPrice(t.exitPrice)}
              </td>
              <td className="muted">{t.exitReason}</td>
              <td className={t.pnl >= 0 ? 'up' : 'down'}>{fmtPnl(t.pnl,currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function LearningInfo({ learning }: { learning: BotLearning }) {
  const edges = Object.entries(learning.edges)
  const validated = edges.filter(([, e]) => e === 'validated').map(([s]) => s)
  const rejected = edges.filter(([, e]) => e === 'rejected').map(([s]) => s)
  const boosted = Object.entries(learning.sizeMult).filter(([, m]) => m > 1)
  const hasContent =
    learning.blocks.length > 0 || validated.length > 0 || rejected.length > 0 || (learning.autoTrainPending?.length ?? 0) > 0
  if (!hasContent) return null
  return (
    <div className="learning-info">
      <p className="muted small">
        🧠 Aprendizaje ·{' '}
        {validated.length > 0 && (
          <span title={validated.join(', ')}>
            {validated.length} con ventaja validada
          </span>
        )}
        {rejected.length > 0 && (
          <>
            {validated.length > 0 && ' · '}
            <span
              className="down"
              title={`${rejected.join(', ')}: sin validación vigente; pueden explorar a tamaño reducido`}
            >
              {rejected.length} sin validación (modo a prueba)
            </span>
          </>
        )}
        {boosted.length > 0 && (
          <span>
            {' · tamaño ↑ en '}
            {boosted.map(([s, m]) => `${s} ×${m}`).join(', ')}
          </span>
        )}
        {!!learning.autoTrainPending?.length && (
          <span title={learning.autoTrainPending.join(', ')}>
            {' · autoentrenamiento: '}{learning.autoTrainPending.length} mercados pendientes
          </span>
        )}
        {learning.nextRetrainAt && (
          <span className="muted">
            {' · próximo reentreno '}
            {new Date(learning.nextRetrainAt * 1000).toLocaleTimeString('es-ES', {
              hour: '2-digit',
              minute: '2-digit',
            })}
          </span>
        )}
      </p>
      {learning.blocks.length > 0 && (
        <ul className="training-list">
          {learning.blocks.map((b) => (
            <li key={`${b.symbol}-${b.direction ?? 'all'}`} className="small down">
              ⛔ {b.symbol}
              {b.direction ? (b.direction === 'long' ? ' largos' : ' cortos') : ''}{' '}
              <span className="muted">— {b.reason}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function TrainingInfo({ training }: { training: TrainingStatus }) {
  if (training.running)
    return (
      <p className="muted small training-status">
        🎓 Entrenando… {training.progress}
        {training.current ? ` · ${training.current}` : ''} (backtest walk-forward
        sobre histórico real; puede tardar varios minutos)
      </p>
    )
  if (training.saved.length === 0) return null
  const applied = training.saved.filter((s) => s.applied)
  return (
    <div className="training-status">
      <p className="muted small">
        🎓 Última calibración:{' '}
        {applied.length > 0
          ? `${applied.length}/${training.saved.length} símbolos con parámetros validados`
          : 'ninguna configuración superó la validación (se usan los parámetros por defecto)'}
      </p>
      {applied.length > 0 && (
        <ul className="training-list">
          {applied.map((s) => (
            <li key={s.symbol} className="small">
              <strong>{s.symbol}</strong>{' '}
              <span className="muted">
                umbral {s.params.entryThreshold} · SL {s.params.slAtr}×ATR · TP{' '}
                {s.params.tpAtr}×ATR · trail {s.params.trailAtr}×ATR
              </span>{' '}
              <span className={s.metrics.validation.pnl >= 0 ? 'up' : 'down'}>
                validación {fmtPnl(s.metrics.validation.pnl)} (
                {s.metrics.validation.trades} ops)
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function BotCard({
  bot,
  onChanged,
}: {
  bot: BotStatus
  onChanged: (b: BotStatus) => void
}) {
  const [trades, setTrades] = useState<BotTrade[] | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [busy, setBusy] = useState(false)
  const [training, setTraining] = useState<TrainingStatus | null>(null)
  const [detailTrade, setDetailTrade] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () =>
      fetchTraining(bot.id)
        .then((t) => {
          if (!cancelled) setTraining(t)
        })
        .catch(() => {})
    load()
    const id = window.setInterval(load, REFRESH_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [bot.id])

  const train = async () => {
    setBusy(true)
    try {
      await startTraining(bot.id)
      setTraining(await fetchTraining(bot.id))
    } catch (e) {
      console.error('train', e)
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (!showHistory) return
    let cancelled = false
    const load = () =>
      fetchBotTrades(bot.id)
        .then((t) => {
          if (!cancelled) setTrades(t)
        })
        .catch(() => {})
    load()
    const id = window.setInterval(load, REFRESH_MS * 2)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [showHistory, bot.id, bot.stats.closedTrades])

  const act = async (action: 'start' | 'stop' | 'reset') => {
    if (action === 'reset' && !window.confirm(`¿Reiniciar ${bot.name}? Se borra su historial y vuelve al balance inicial.`))
      return
    setBusy(true)
    try {
      onChanged(await botAction(bot.id, action))
    } catch (e) {
      console.error('bot action', e)
    } finally {
      setBusy(false)
    }
  }

  const totalPnl = bot.equity - bot.initialBalance
  const { stats, config } = bot
  const blocked = Object.entries(bot.diagnostics ?? {}).filter(([symbol]) => !bot.openPositions.some(p => p.symbol === symbol))
  const reasons = blocked.reduce<Record<string, number>>((counts, [, diagnostic]) => {
    const reason = diagnostic.reason.startsWith('Señal ') && diagnostic.reason.includes('no alcanza umbral') ? 'Señal todavía insuficiente' : diagnostic.reason
    counts[reason] = (counts[reason] ?? 0) + 1
    return counts
  }, {})
  const mainReasons = Object.entries(reasons).sort((a, b) => b[1] - a[1]).slice(0, 3)

  return (
    <div className="panel bot-card">
      <div className="bot-head">
        <h3>
          <span className={`bot-dot ${bot.running ? 'on' : 'off'}`} />
          {bot.name} <span className="muted">· {bot.horizon}</span>
        </h3>
        <div className="bot-actions">
          <button
            disabled={busy || training?.running}
            onClick={train}
            title="Optimiza umbral, SL, TP y trailing por símbolo con backtest walk-forward sobre histórico real"
          >
            🎓 Entrenar
          </button>
          <button disabled={busy} onClick={() => act(bot.running ? 'stop' : 'start')}>
            {bot.running ? '⏸ Pausar' : '▶ Reanudar'}
          </button>
          <button disabled={busy} className="danger" onClick={() => act('reset')}>
            ↺ Reset
          </button>
        </div>
      </div>

      <div className="bot-stats">
        <div>
          <small className="muted">Equity</small>
          <strong>{fmtMoney(bot.equity,bot.currency)}</strong>
        </div>
        <div>
          <small className="muted">PnL total</small>
          <strong className={totalPnl >= 0 ? 'up' : 'down'}>{fmtPnl(totalPnl,bot.currency)}</strong>
        </div>
        <div>
          <small className="muted">Operaciones</small>
          <strong>{stats.closedTrades}</strong>
        </div>
        <div>
          <small className="muted">Acierto</small>
          <strong>{stats.winRate != null ? `${stats.winRate}%` : '—'}</strong>
        </div>
        <div>
          <small className="muted">Abiertas</small>
          <strong>
            {bot.openPositions.length}/{config.maxPositions}
          </strong>
        </div>
      </div>

      {bot.session && <p className="muted small">Sesión nueva desde {fmtMoney(bot.initialBalance,bot.currency)} · conversiones de divisa fijadas al inicio · el tamaño se ajusta al patrimonio.</p>}
      {bot.risk && (
        <p className={`small risk-line ${bot.risk.entriesPaused ? 'down' : 'muted'}`}>
          🛡️ PnL hoy: <span className={bot.risk.dailyPnl >= 0 ? 'up' : 'down'}>
            {fmtPnl(bot.risk.dailyPnl,bot.currency)}
          </span>{' '}
          (límite diario -{fmtMoney(bot.risk.dailyLossLimit,bot.currency)})
          {bot.risk.entriesPaused &&
            ' · ⛔ entradas pausadas hasta mañana por límite de pérdidas'}
          {!bot.risk.entriesPaused && bot.risk.riskScale < 1 &&
            ` · ⚠️ tamaño al ${bot.risk.riskScale * 100}% tras ${bot.risk.consecLosses} pérdidas seguidas`}
        </p>
      )}

      {config.requireValidation === false && config.probationSizePct != null && (
        <p className="small muted">Mercados sin validar: a prueba, hasta el {config.probationSizePct}% del riesgo habitual y un máximo de {config.maxProbationPositions} posiciones a prueba por bot.</p>
      )}
      {bot.diagnostics && (
        <p className="small execution-status">{bot.openPositions.length > 0 ? `Gestionando ${bot.openPositions.length} posiciones. ` : 'Sin posición abierta. '}
          {mainReasons.length > 0 && <>Nuevas entradas: {mainReasons.map(([reason, count]) => `${reason} (${count})`).join(' · ')}.</>}
        </p>
      )}
      {bot.diagnostics && (
        <details className="small muted">
          <summary>Diagnóstico de entradas por mercado</summary>
          <ul>
            {Object.entries(bot.diagnostics).map(([symbol, d]) => (
              <li key={symbol}>
                {symbol}: {d.reason}{d.score != null ? ` (señal ${d.score.toFixed(3)})` : ''}
                {' · '}{fmtTime(d.at)}
              </li>
            ))}
          </ul>
        </details>
      )}
      <PositionsTable bot={bot} onSelect={setDetailTrade} />

      {bot.learning && <LearningInfo learning={bot.learning} />}
      {training && <TrainingInfo training={training} />}

      <div className="bot-foot">
        <button className="link-btn" onClick={() => setShowHistory((v) => !v)}>
          {showHistory ? 'Ocultar historial ▲' : `Ver historial (${stats.closedTrades}) ▼`}
        </button>
        <small className="muted">
          Velas {config.workInterval} · riesgo {config.riskPct}%/op (sin tope de
          margen) · apalancamiento ×{config.maxLeverage} · SL {config.slAtr}×ATR ·
          TP {config.tpAtr}×ATR · trailing {config.trailAtr}×ATR ·{' '}
          {config.sentimentWeight}% opinión pública
          {bot.trainedSymbols.length > 0 &&
            ` · entrenado: ${bot.trainedSymbols.join(', ')}`}
        </small>
      </div>
      {showHistory && <TradesTable trades={trades ?? []} onSelect={setDetailTrade} currency={bot.currency} />}
      {detailTrade != null && (
        <TradeDetailModal
          botId={bot.id}
          tradeId={detailTrade}
          currency={bot.currency}
          onClose={() => setDetailTrade(null)}
        />
      )}
    </div>
  )
}

export default function BotsPanel() {
  const [bots, setBots] = useState<BotStatus[]>([])
  const [error, setError] = useState(false)

  const refresh = useCallback(() => {
    fetchBots()
      .then((b) => {
        setBots(b)
        setError(false)
      })
      .catch(() => setError(true))
  }, [])

  useEffect(() => {
    refresh()
    const id = window.setInterval(refresh, REFRESH_MS)
    return () => window.clearInterval(id)
  }, [refresh])

  const onChanged = (updated: BotStatus) =>
    setBots((prev) => prev.map((b) => (b.id === updated.id ? updated : b)))

  if (bots.length === 0) return <p role="status">{error ? 'No se puede actualizar el Laboratorio simulado.' : 'Consultando los bots del Laboratorio…'}</p>

  return (
    <section className="bots-section">
      <div className="bots-title">
        <h3>🤖 Laboratorio simulado · {bots.reduce((sum, bot) => sum + bot.openPositions.length, 0)} posiciones abiertas</h3>
        <small className="muted">
          Cuenta simulada (paper trading) sobre precios reales · no es dinero real ni
          asesoramiento financiero
        </small>
      </div>
      {error && <p role="status">No se puede actualizar el Laboratorio; se conserva la última lectura.</p>}
      <p className="execution-status">{bots.filter(bot => bot.running).length} de {bots.length} bots activos · {bots.reduce((sum, bot) => sum + bot.stats.closedTrades, 0)} operaciones cerradas en estas cuentas simuladas. Sus resultados se registran aquí; MT5 tiene otra cuenta y otro historial.</p>
      {bots.some(bot => bot.config.strategyProfile?.startsWith('aggressive')) && <p className="execution-status">Perfil agresivo ampliado en evaluación. Los mercados sin validar pueden usar hasta el {Math.max(...bots.map(bot => bot.config.probationSizePct ?? 0))}% del riesgo habitual y hasta {Math.max(...bots.map(bot => bot.config.maxProbationPositions ?? 0))} posiciones a prueba por bot. Los límites diarios, de margen y de riesgo conjunto siguen aplicándose al tamaño final.</p>}
      <div className="bots-grid">
        {bots.map((b) => (
          <BotCard key={b.id} bot={b} onChanged={onChanged} />
        ))}
      </div>
      <BotPerformance />
    </section>
  )
}
