import { useEffect, useState } from 'react'
import type { PriceSocket } from '../api'
import type { AgentThought, MakerState, SocketStatus, SwarmStatus } from '../types'
import './LiveSwarmTicker.css'

const clock = (at: number) => new Date(at * 1000).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
const percent = (n: unknown, digits = 0) => typeof n === 'number' && Number.isFinite(n) ? `${(n * 100).toLocaleString('es-ES', { maximumFractionDigits: digits })} %` : '—'
const metric = (value: unknown, digits = 2) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—'

export default function LiveSwarmTicker({ socket, maker, market }: { socket: PriceSocket; maker?: MakerState; market: string }) {
  const [messages, setMessages] = useState<AgentThought[]>([])
  const [status, setStatus] = useState<SwarmStatus | null>(null)
  const [connection, setConnection] = useState<SocketStatus>('connecting')
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    let cancelled = false
    let version = 0
    const stopThoughts = socket.onAgentThought(event => {
      if (event.desk !== market) return
      setMessages(previous => [{ ...event }, ...previous.filter(row => !(row.streamId === event.streamId && row.sequence === event.sequence))].slice(0, 80))
    })
    const stopStatus = socket.onSwarmStatus(event => { version++; setStatus(event.data) })
    const load = async () => {
      const before = version
      try {
        const response = await fetch('/api/agents/swarm')
        if (response.ok) {
          const data: SwarmStatus = await response.json()
          if (!cancelled && before === version) setStatus(data)
        }
      } catch { /* La conexión conserva y marca la última lectura. */ }
    }
    const stopConnection = socket.onStatus(state => { setConnection(state); if (state === 'connected') void load() })
    void load()
    const reconcile = window.setInterval(() => { void load() }, 30_000)
    return () => { cancelled = true; stopThoughts(); stopStatus(); stopConnection(); window.clearInterval(reconcile) }
  }, [socket, market])
  const hasPending = Boolean(maker?.pending.length)
  useEffect(() => {
    if (!hasPending) return
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 250)
    return () => window.clearInterval(timer)
  }, [hasPending])
  const grok = messages.filter(row => row.agent.toLowerCase().includes('grok'))
  const gpt = messages.filter(row => /gpt|chatgpt/i.test(row.agent))
  const local = messages.find(row => row.agent === 'Motor-Quant-Local' || row.agent === 'Mesa-Siete')
  const subscription = status?.transport === 'subscription'
  const decisions = status?.decisions?.filter(row => row && (market === 'crypto' ? row.symbol !== 'SPY' : market === 'stocks' ? row.symbol === 'SPY' : false)) ?? []
  const renderMessages = (rows: AgentThought[], provider: string) => rows.length ? rows.slice(0, 6).map(row => <article className={`swarm-message phase-${row.phase}`} key={`${row.streamId}-${row.sequence}`}>
    <div><b>{row.agent}</b><time>{clock(row.at)}</time></div><p>{row.detail}</p>
    {row.phase === 'response' && <small>Lectura del modelo · pendiente de controles locales</small>}
    {row.report && <div className="swarm-metrics">
      {'bias' in row.report && <span>Sesgo en X <b>{metric(row.report.bias)}</b></span>}
      {'imbalance' in row.report && <span>Desbalance <b>{metric(row.report.imbalance)}</b></span>}
      {'confidence' in row.report && <span>Confianza de lectura <b>{percent(row.report.confidence)}</b></span>}
      {'conviction' in row.report && <span>Convicción <b>{percent(row.report.conviction)}</b></span>}
    </div>}
    {row.verdict && <><div className="swarm-metrics"><span>Riesgo ajustado <b>{percent(row.verdict.adjusted_risk_fraction, 2)}</b></span><span>Kelly observado <b>{metric(row.verdict.kelly_score, 3)}</b></span><span className={row.verdict.approved ? 'swarm-approved' : 'swarm-veto'}>{row.verdict.approved ? 'Revisión favorable' : 'Entrada vetada'}</span></div>{row.verdict.friction_warning && <small>{row.verdict.friction_warning}</small>}</>}
  </article>) : <p className="swarm-empty">{status?.configured ? `Esperando la próxima lectura de ${provider}.` : `${provider} espera su conexión. Aquí aparecerán sus lecturas y veredictos.`}</p>
  return <section className="live-swarm" aria-labelledby="swarm-title">
    <div className="swarm-heading"><div><span className="kitchen-eyebrow">DOS CHEFS, UNA DECISIÓN</span><h2 id="swarm-title">Sala de control IA en vivo</h2><p>Señal, flujo y riesgo, a la vista de la mesa.</p></div><span className={`swarm-connection ${connection === 'connected' ? 'is-connected' : ''}`}><i />{connection === 'connected' ? 'Conectado en vivo' : connection === 'connecting' ? 'Reconectando…' : 'Sin conexión · última lectura'}</span></div>
    <div className="swarm-mode"><strong>{status?.mode === 'paper' && status.armed ? 'PAPER ARMADO' : 'MODO SOMBRA'}</strong><span>{!status ? 'Comprobando conexiones…' : !status.configured ? subscription ? `Inicia sesión en ${[!status.subscription?.grokReady && 'Grok Build', !status.subscription?.chatgptReady && 'Codex con ChatGPT'].filter(Boolean).join(' y ')}.` : `Falta configurar ${[!status.xaiConfigured && 'xAI', !status.openaiConfigured && 'OpenAI'].filter(Boolean).join(' y ')} en el servidor.` : !status.enabled ? 'Observación automática pausada.' : status.running ? subscription ? 'Transporte por suscripción preparado. Comprueba las respuestas y entregas recientes en el panel del equipo.' : 'Enjambre observando dentro de su presupuesto.' : 'Observación preparada; esperando el ciclo.'}</span>{status && <small>{status.remaining24h ?? 0} ciclos disponibles · {subscription ? 'Cupo de tus planes · sin fallback a APIs de pago' : `${metric(status.estimatedCostMonthUSD)} / ${status.monthlyCapUSD ?? 15} USD este mes`}</small>}</div>
    <div className="swarm-debate"><div className="swarm-chef is-grok"><header><span className="swarm-avatar" aria-hidden="true">G</span><div><h3>Chef Grok</h3><p>X en tiempo real · flujo · coordinación</p></div></header><div className="swarm-feed">{renderMessages(grok, 'Grok')}</div></div><div className="swarm-chef is-gpt"><header><span className="swarm-avatar" aria-hidden="true">C</span><div><h3>Chef ChatGPT</h3><p>Riesgo cuantitativo · fricción · expectativa</p></div></header><div className="swarm-feed">{renderMessages(gpt, 'ChatGPT')}</div></div></div>
    {local && <div className="swarm-local"><b>{local.agent === 'Mesa-Siete' ? `Quorum${local.score != null ? ` · S ${local.score.toFixed(3)}` : ''}` : 'Auditoría local de la propuesta'}</b><span>{local.detail}</span><time>{clock(local.at)}</time></div>}
    {decisions.length > 0 && <div className="swarm-decisions">{decisions.map(row => row && <div key={row.symbol}><strong>{row.symbol}</strong><b>S {metric(row.score, 3)}</b><span>{row.profile}</span><p>{row.reason}</p><time>{clock(row.at)}</time></div>)}</div>}
    <div className="swarm-maker"><div className="swarm-maker-title"><h3>El pase maker <span>Cola simulada</span></h3><small>Fill observado · plazo de {maker?.ttlSeconds ?? 60} s</small></div>
      {!hasPending ? <p className="swarm-empty">El pase está libre. Las próximas órdenes limitadas aparecerán aquí.</p> : maker!.pending.map(order => {
        const fill = Math.min(100, Math.max(0, order.quantity > 0 ? order.filled / order.quantity * 100 : 0))
        const remaining = Math.max(0, order.expiresAt - now)
        const ttl = maker?.ttlSeconds ?? 60
        return <article className="swarm-order" key={`${order.symbol}-${order.side}-${order.entryKey ?? order.expiresAt}`}><div className="swarm-order-head"><strong>{order.symbol} <small>{order.side === 'buy' ? 'Compra' : 'Salida parcial'}</small></strong><b>{Math.ceil(remaining)} s <small>{remaining === 0 ? 'esperando resolución' : 'restantes'}</small></b></div><div className="swarm-progress" role="progressbar" aria-label={`Llenado de ${order.symbol}`} aria-valuenow={fill} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${fill}%` }} /></div><div className="swarm-fill-label"><span>{order.filled.toLocaleString('es-ES', { maximumSignificantDigits: 6 })} / {order.quantity.toLocaleString('es-ES', { maximumSignificantDigits: 6 })} unidades</span><strong>{fill.toFixed(1)} %</strong></div><div className="swarm-ttl" aria-hidden="true"><span style={{ width: `${Math.min(100, remaining / ttl * 100)}%` }} /></div><p className="swarm-pressure">{connection !== 'connected' ? 'Última lectura del libro; conexión pendiente.' : !order.bookFresh || order.pressure == null ? 'Presión no disponible: libro pendiente o caducado.' : `Presión del mejor bid/ask: ${order.pressure > 0 ? 'compradora' : order.pressure < 0 ? 'vendedora' : 'equilibrada'} (${metric(order.pressure)}).`}</p></article>
      })}
      {maker?.history?.length ? <p className="swarm-last-order">Última orden: {maker.history.at(-1)!.symbol} · {maker.history.at(-1)!.status} · {maker.history.at(-1)!.filled.toLocaleString('es-ES')} unidades observadas.</p> : null}
    </div><p className="swarm-footnote">Las lecturas muestran conclusiones y métricas publicadas por cada agente. La convicción no es una probabilidad de beneficio. El código local conserva los vetos.</p>
  </section>
}
