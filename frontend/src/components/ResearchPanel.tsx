import { useCallback, useEffect, useRef, useState } from 'react'
import './ResearchPanel.css'

interface Portfolio {
  capitalEUR: number; initialEUR: number; openRiskEUR: number; grossExposureEUR: number
  pendingReservations: number; updatedAt: number; scope: string
  exposureComplete?: boolean; unknownExposurePositions?: number
  policy: { maxRiskPct: number; maxFactorRiskPct: number; maxGrossMultiple: number; maxFactorGrossMultiple: number }
  accounts: { source: string; bot: string; equityEUR: number; initialEUR: number }[]
  factors: Record<string, { riskEUR: number; notionalEUR: number; positions: number }>
}
interface Metrics {
  pnl: number; trades?: number; fees?: number; carry?: number; maxDrawdown?: number
  winRate?: number | null; profitFactor?: number | null; turnover?: number
}
interface Strategy { id: string; family: string; lookback: number; volatility_managed: boolean }
interface Candidate {
  strategy: Strategy; discoveryPnl: number; trades: number; discoveryStressPnl: number
  positiveBlocks: number; maxBlockDrawdown: number
}
interface Dataset {
  symbol: string; interval: string; source?: string; bars?: number; ok: boolean; reason: string
  dataStart?: number; dataEnd?: number; selected?: Strategy | null; candidates?: Candidate[]
  candidateEligible?: boolean; trialCount?: number; datasetHash?: string
  holdout?: (Metrics & { stress: Metrics; uncertainty: { lower95: number | null; upper95: number | null; sample: number } }) | null
  benchmarks?: { cash: Metrics; buyHold: Metrics | null } | null
  costModel?: { feeRate: number; slippageRate: number; spreadPct: number; carryDailyRate: number; verified: boolean }
}
interface ResearchResult {
  version: string; generatedAt: number; request: { symbols: string[]; intervals: string[]; days: number; maxBars: number }
  results: Dataset[]; assumptions: string[]
}
interface ResearchJob {
  status: 'idle' | 'running' | 'completed' | 'failed'; startedAt?: number; finishedAt?: number
  error?: string | null; result?: ResearchResult | null
}

const amount = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const percent = (value: number) => `${amount(value * 100)} %`
const stamp = (value: number) => new Date(value * 1000).toLocaleString('es-ES', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
const signed = (value: number | null | undefined) => value == null ? '—' : `${value > 0 ? '+' : ''}${amount(value)}`
const names: Record<string, string> = { donchian: 'Rupturas de rango', momentum: 'Seguimiento de impulso', 'momentum-vol': 'Impulso con riesgo por volatilidad', 'range-reversion': 'Reversión en rango' }
const factors: Record<string, string> = { gold: 'Oro', crypto: 'Criptomonedas', 'us-equities': 'Bolsa de EE. UU.', fx: 'Divisas', energy: 'Energía' }
const sources: Record<string, string> = { desk: 'Mesa Siete', lab: 'Laboratorio', mt5: 'MT5 demo' }
const states = { idle: 'Sin comparar todavía', running: 'Cocinando el estudio', completed: 'Estudio terminado', failed: 'Estudio interrumpido' }

async function readJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, { signal: AbortSignal.timeout(10_000), ...options })
  if (!response.ok) throw new Error(`Respuesta ${response.status}`)
  return response.json()
}

function RiskMeter({ label, value, limit, suffix }: { label: string; value: number; limit: number; suffix: string }) {
  const ratio = limit > 0 ? value / limit : value > 0 ? 1 : 0
  return <div className={`research-risk-meter ${ratio >= 1 ? 'at-limit' : ''}`}>
    <div><span>{label}</span><strong>{amount(value)} <small>/ {amount(limit)} {suffix}</small></strong></div>
    <div className="research-meter-track" role="meter" aria-label={label} aria-valuenow={Math.max(0, value)} aria-valuemin={0} aria-valuemax={Math.max(limit, value, .01)}><i style={{ width: `${Math.min(100, Math.max(0, ratio * 100))}%` }} /></div>
  </div>
}

function DatasetReport({ row }: { row: Dataset }) {
  const winner = row.selected
  const final = row.holdout
  const cost = row.costModel
  return <details className={`research-dataset ${row.candidateEligible ? 'is-candidate' : ''}`}>
    <summary><span className="research-dataset-symbol">{row.symbol}<small>{row.interval}</small></span><span className="research-dataset-description">{winner ? names[winner.family] ?? winner.family : row.ok ? 'Se conserva el efectivo' : 'Datos insuficientes o no disponibles'}<small>{row.bars ? `${row.bars.toLocaleString('es-ES')} velas · ${row.trialCount ?? 12} variantes` : row.reason}</small></span><span className={`research-badge ${row.candidateEligible ? 'positive' : ''}`}>{row.candidateEligible ? 'A seguimiento' : winner ? 'Sin promoción' : 'Sin candidata'}</span><span className="research-chevron" aria-hidden="true">⌄</span></summary>
    <div className="research-dataset-body">
      <p className="research-reason">{row.reason}</p>
      {row.dataStart && row.dataEnd && <p className="research-small">Datos efectivos: {stamp(row.dataStart)} → {stamp(row.dataEnd)} · {row.source}</p>}
      {final && <>
        <div className="research-final-metrics">
          <div><span>Resultado neto · tramo reservado</span><strong className={final.pnl >= 0 ? 'positive' : 'negative'}>{signed(final.pnl)} <small>USDT</small></strong><small>{final.trades ?? 0} operaciones · {winner?.id}</small></div>
          <div><span>Con costes duplicados</span><strong className={final.stress.pnl >= 0 ? 'positive' : 'negative'}>{signed(final.stress.pnl)} <small>USDT</small></strong><small>Prueba de fricción</small></div>
          <div><span>Mayor caída del tramo</span><strong>{amount(final.maxDrawdown)} <small>USDT</small></strong><small>Comisiones: {amount(final.fees)} · Financiación: {amount(final.carry)}</small></div>
        </div>
        <p className="research-small">Expectativa por operación, intervalo descriptivo del 95 %: {final.uncertainty.lower95 == null ? 'muestra insuficiente' : `${amount(final.uncertainty.lower95)} a ${amount(final.uncertainty.upper95)} USDT`}. No corrige la selección entre múltiples mercados.</p>
      </>}
      {row.benchmarks && <div className="research-benchmarks"><strong>Mismo tramo · referencia 100 USDT</strong><span>Efectivo: {signed(row.benchmarks.cash.pnl)} USDT</span><span>Comprar y mantener: {signed(row.benchmarks.buyHold?.pnl)} USDT</span><small>La exposición de las estrategias puede ser menor.</small></div>}
      {cost && <p className="research-costs">Escenario por entrada y salida: comisión {percent(cost.feeRate)}, deslizamiento {percent(cost.slippageRate)} y spread completo {percent(cost.spreadPct)}. Spot largo, sin apalancamiento ni préstamo. Tarifas de cuenta {cost.verified ? 'verificadas' : 'sin verificar'}.</p>}
      {!!row.candidates?.length && <div className="research-table-wrap"><table className="research-table"><caption>Comparación antes de consultar el tramo reservado</caption><thead><tr><th>Familia / variante</th><th>Neto</th><th>Costes ×2</th><th>Operaciones</th><th>Bloques positivos</th></tr></thead><tbody>{row.candidates.map(candidate => <tr key={candidate.strategy.id} className={candidate.strategy.id === winner?.id ? 'is-selected' : ''}><td><strong>{names[candidate.strategy.family] ?? candidate.strategy.family}</strong><small>{candidate.strategy.id}{candidate.strategy.id === winner?.id ? ' · Selección congelada' : ''}</small></td><td className={candidate.discoveryPnl >= 0 ? 'positive' : 'negative'}>{signed(candidate.discoveryPnl)}</td><td className={candidate.discoveryStressPnl >= 0 ? 'positive' : 'negative'}>{signed(candidate.discoveryStressPnl)}</td><td>{candidate.trades}</td><td>{candidate.positiveBlocks}/3</td></tr>)}</tbody></table></div>}
    </div>
  </details>
}

export default function ResearchPanel() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null)
  const [job, setJob] = useState<ResearchJob | null>(null)
  const [riskError, setRiskError] = useState<string | null>(null)
  const [researchError, setResearchError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const mounted = useRef(false)
  const inFlight = useRef(false)
  const refresh = useCallback(async () => {
    if (inFlight.current) return
    inFlight.current = true
    try {
      const [risk, research] = await Promise.allSettled([readJson<Portfolio>('/api/portfolio/risk'), readJson<ResearchJob>('/api/research')])
      if (!mounted.current) return
      if (risk.status === 'fulfilled') { setPortfolio(risk.value); setRiskError(null) }
      else setRiskError('Riesgo pendiente de actualizar. Se conserva la última lectura.')
      if (research.status === 'fulfilled') { setJob(research.value); setResearchError(null) }
      else setResearchError('No se pudo actualizar el estudio. Se reintentará automáticamente.')
    } finally { inFlight.current = false }
  }, [])
  useEffect(() => {
    mounted.current = true
    void refresh()
    const timer = window.setInterval(() => { void refresh() }, 10_000)
    return () => { mounted.current = false; window.clearInterval(timer) }
  }, [refresh])
  const startResearch = async () => {
    setStarting(true)
    setResearchError(null)
    try {
      const next = await readJson<ResearchJob>('/api/research/run', { method: 'POST' })
      if (mounted.current) setJob(next)
      void refresh()
    } catch { if (mounted.current) setResearchError('No se pudo iniciar la comparación. Comprueba el estado antes de repetirla.') }
    finally { if (mounted.current) setStarting(false) }
  }
  const report = job?.result
  const results = report?.results ?? []
  const candidates = results.filter(result => result.candidateEligible).length
  const running = starting || job?.status === 'running'
  return <section className="research-panel" aria-label="Riesgo conjunto e investigación de estrategias">
    <article className="research-risk-card" aria-labelledby="research-risk-title">
      <div className="research-card-heading"><span className="research-heading-icon" aria-hidden="true">◈</span><div><span className="research-eyebrow">EL PRESUPUESTO DE TODA LA COCINA</span><h2 id="research-risk-title">Riesgo coordinado</h2></div><span className={`research-badge ${riskError ? 'is-stale' : ''}`}>{riskError ? 'Lectura pendiente' : portfolio ? `${portfolio.accounts.length} cuentas` : 'Conectando'}</span></div>
      {portfolio ? <>
        <div className="research-risk-overview"><div className="research-capital"><span>Patrimonio agregado</span><strong>{amount(portfolio.capitalEUR)} <small>EUR</small></strong><p>{portfolio.accounts.length} cuentas independientes · {amount(portfolio.initialEUR)} € iniciales en conjunto.</p></div><div className="research-risk-meters"><RiskMeter label="Riesgo abierto" value={portfolio.openRiskEUR} limit={portfolio.capitalEUR * portfolio.policy.maxRiskPct / 100} suffix="EUR" /><RiskMeter label="Exposición bruta" value={portfolio.grossExposureEUR} limit={portfolio.capitalEUR * portfolio.policy.maxGrossMultiple} suffix="EUR" /></div></div>
        <div className="research-factor-list">{Object.entries(portfolio.factors).map(([factor, values]) => <span key={factor}><i />{factors[factor] ?? factor}<strong>{amount(values.riskEUR)} €</strong><small>{values.positions} pos.</small></span>)}{!Object.keys(portfolio.factors).length && <span>Sin posiciones abiertas.</span>}</div>
        <details className="research-account-details"><summary>Ver cuentas y límites <span aria-hidden="true">⌄</span></summary><div className="research-account-grid">{portfolio.accounts.map(account => <div key={`${account.source}:${account.bot}`}><span>{sources[account.source] ?? account.source}{account.source === 'lab' ? ` · ${account.bot}` : ''}</span><strong>{amount(account.equityEUR)} €</strong><small>Inicial: {amount(account.initialEUR)} €</small></div>)}</div><p>Riesgo conjunto máximo {portfolio.policy.maxRiskPct} %; por factor {portfolio.policy.maxFactorRiskPct} %. Nominal máximo {portfolio.policy.maxGrossMultiple}× el patrimonio; por factor {portfolio.policy.maxFactorGrossMultiple}×. Las posiciones previas se siguen gestionando.</p></details>
        {portfolio.exposureComplete === false && <p className="research-error" role="status">{portfolio.unknownExposurePositions} posiciones sin nominal verificado. La exposición mostrada es parcial; nuevas entradas bloqueadas hasta reconciliarlas.</p>}
        <p className="research-footnote">La suma de estas cuentas virtuales no representa un único depósito de 100 €. {portfolio.pendingReservations > 0 ? `${portfolio.pendingReservations} órdenes pendientes reservan presupuesto. ` : ''}Última lectura: {stamp(portfolio.updatedAt)}.</p>
      </> : <p className="research-empty">Consultando el presupuesto de las cuentas…</p>}
      {riskError && <p className="research-error" role="status">{riskError}</p>}
    </article>
    <details className="research-notebook">
      <summary><span className="research-heading-icon" aria-hidden="true">▤</span><div><span className="research-eyebrow">PROBAR LA RECETA ANTES DE SERVIRLA</span><h2>Comparador de estrategias</h2><p>Costes completos, periodos separados y efectivo como alternativa.</p></div><span className={`research-badge ${running ? 'is-running' : ''}`}>{running ? <><i />Estudio en curso</> : report ? `${candidates} candidatas a seguimiento` : 'Preparado para comparar'}</span><span className="research-chevron" aria-hidden="true">⌄</span></summary>
      <div className="research-notebook-body">
        <div className="research-run-bar"><div><strong>{job ? states[job.status] : 'Consultando el último estudio'}</strong><p>BTC, ETH y SOL · 5 min, 15 min y 1 h · últimos 30 días · máximo 9.000 velas por muestra.</p></div><button type="button" onClick={() => { void startResearch() }} disabled={running}>{running ? 'Comparando…' : 'Comparar estrategias'}<span aria-hidden="true">{running ? '◌' : '↗'}</span></button></div>
        <div className="research-method"><span><b>01</b>Rupturas de rango</span><span><b>02</b>Impulso</span><span><b>03</b>Riesgo por volatilidad</span><span><b>04</b>Reversión en rango</span></div>
        <p className="research-explanation">Se comparan variantes predefinidas con tres bloques temporales. La seleccionada se congela antes de consultar el tramo final reservado. Se exigen costes duplicados, estabilidad y muestra suficiente. Los resultados se expresan sobre <strong>100 USDT de referencia</strong>.</p>
        {researchError && <p className="research-error" role="status">{researchError}</p>}
        {job?.error && <p className="research-error" role="status">{job.error}</p>}
        {running && <div className="research-progress" role="status"><span /><p>Descargando precios y reproduciendo cada estrategia. El informe aparecerá aquí al terminar.</p></div>}
        {report && <><div className="research-result-heading"><strong>{results.length} muestras comparadas · {candidates} candidatas a seguimiento</strong><span>{stamp(report.generatedAt)}</span></div><div className="research-datasets">{results.map(row => <DatasetReport key={`${row.symbol}:${row.interval}`} row={row} />)}</div><details className="research-assumptions"><summary>Supuestos y alcance del experimento</summary><ul>{report.assumptions.map(item => <li key={item}>{item}</li>)}</ul></details></>}
        {!report && !running && <p className="research-empty">Aún no hay un informe. La comparación no abre operaciones ni cambia las cuentas.</p>}
        <p className="research-footnote">Una candidata pasa a seguimiento futuro; su resultado histórico no activa una estrategia ni acredita rentabilidad futura. Repetir el mismo periodo no añade evidencia independiente.</p>
      </div>
    </details>
  </section>
}
