import { useEffect, useRef, useState, type FormEvent } from 'react'
import './ProfessionalPanel.css'

interface Challenge {
  agent?: string; state: string; taskId?: string; correlationId?: string
  createdAt?: number; observedAt?: number
}
interface RecentTask { taskId: string; correlationId?: string; status: string; updatedAt?: number }
interface TeamHealth {
  observedAt: number
  integration: { localProfiles?: string[]; desktopAgentsSeen?: string[]; challenges?: Challenge[]; pendingAgents?: string[] }
  provider?: { providers?: { provider: string; installed: boolean; state: string; lastObservedAt?: number; lastSuccessAt?: number }[] }
  recentTasks?: RecentTask[]; tasksByStatus?: Record<string, number>; receipts?: number; failedReceipts?: number
  limits?: { tasksPerDay?: number }
}
interface Stage {
  stage: string; attempt: number; status: string; observedAt?: number
  actor?: { kind?: string; desktopBot?: boolean }
  result?: { missingSymbols?: string[]; blockers?: string[]; claimedUrls?: number; retrievedUrls?: number; independentlyVerifiedUrls?: number }
}
interface Task extends RecentTask {
  brief?: string; symbols?: string[]; stages?: Stage[]; proposal?: { blockers?: string[]; validationState?: string }
  error?: string
}
interface Plan { id: string; name: string; interval?: string | null; hypothesis: string; trialCount?: number }
interface Promotion {
  status?: string; independentFuture?: boolean; executionEquivalent?: boolean; costsVerified?: boolean; holdoutReused?: boolean
}
interface Experiment {
  id: string; plan_id?: string; planId?: string; frozen_at?: number; frozenAt?: number; events?: number
  manifest: { symbols?: string[]; benchmark?: string | null; executionEquivalent?: boolean; costModel?: { version?: string; verified?: boolean } }
  latestEvidence?: { kind: string; observedAt?: number; payload: Promotion & { promotion?: Promotion; metrics?: { trades?: number }; samples?: number } } | null
}
interface PlanCatalog { plans: Plan[]; experiments: Experiment[]; ledger: boolean; trials: number }
interface ProfessionalStatus {
  mode?: string; limitations?: string[]; trainingEngine?: string | number; deskPolicy?: string
  progress?: { observedAt?: number; services?: { source: string; id: string; role: string; state: string; ageSeconds?: number | null }[]; stalled?: unknown[] }
}

const ROLES = [
  ['market-radar', 'Radar de mercado', 'Contexto y candidatos'],
  ['news-verifier', 'Verificador de noticias', 'Fuentes y disponibilidad'],
  ['quant-researcher', 'Investigador cuantitativo', 'Hipótesis y experimento'],
  ['technical-reviewer', 'Revisor técnico', 'Fallos y regresiones'],
  ['grok-desk-commander', 'Coordinador', 'Propuesta y bloqueos'],
] as const
const STATES: Record<string, string> = {
  received: 'Recibido', running: 'En revisión', proposal_ready: 'Propuesta preparada', blocked: 'Bloqueado', cancelled: 'Cancelado',
  started: 'Iniciado', completed: 'Contrato completado', skipped: 'Sin activar', error: 'Error', timeout: 'Tiempo agotado', budget: 'Cupo agotado', rejected: 'Rechazado',
  success: 'Respuesta previa observada', unobserved: 'Sin respuesta observada', unavailable: 'No disponible',
  auth: 'Sesión pendiente en la última prueba', quota: 'Cupo agotado en la última prueba', invalid: 'Respuesta inválida', queue_timeout: 'Espera de cliente agotada',
  historical_pass: 'Filtros históricos superados', research_only: 'Solo investigación', forward_eligible: 'Elegible para seguimiento',
  descriptive_pairs: 'Comparación emparejada descriptiva', awaiting_point_in_time_data: 'Faltan observaciones disponibles al decidir',
}
const BLOCKERS: Record<string, string> = {
  radar_missing_or_failed: 'Falta una lectura de radar vigente para el universo solicitado.',
  news_evidence_not_verified: 'Las noticias o citas carecen de comprobación independiente.',
  concrete_research_proposal_missing: 'Falta una hipótesis que cumpla el contrato de investigación.',
  review_contract_failed: 'La revisión técnica no cumple el contrato.',
  desktop_receipt_pending: 'Faltan los acuses de escritorio correspondientes a este encargo.',
  host_or_scheme_not_allowed: 'La fuente no pertenece a los sitios públicos permitidos por este flujo.',
  non_public_dns_address: 'La dirección de la fuente no es pública.',
  redirect_not_allowed: 'La fuente redirige; hace falta una URL directa permitida.',
  task_url_budget: 'Se alcanzó el máximo de cuatro fuentes por encargo.',
  source_timeout: 'La fuente no respondió dentro del tiempo permitido.',
  source_too_large: 'El documento supera el tamaño permitido.',
  source_retrieval_failed: 'No se pudo recuperar la fuente.',
  article_content_unavailable: 'No hay contenido suficiente para revisar la cita.',
  publication_date_missing_or_stale: 'Falta una fecha de publicación válida y vigente.',
  claim_not_corroborated: 'La afirmación sigue pendiente de corroboración factual.',
  editorial_independence_unverified: 'La independencia editorial sigue pendiente de comprobación.',
  daily_task_budget: 'Se alcanzó el límite diario de encargos. No se creó otro encargo.',
}
const statusLabel = (value: string) => STATES[value] ?? 'Estado pendiente de interpretar'
const stamp = (value?: number) => value && Number.isFinite(value) ? new Date(value * 1000).toLocaleString('es-ES', { timeZone: 'Europe/Madrid', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : 'Sin fecha disponible'
const numberOf = (value?: number) => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('es-ES', { maximumFractionDigits: 2 }) : '—'
const symbolsOf = (value: string) => value.trim().toUpperCase().split(/[\s,;]+/).filter(Boolean)
function symbolError(symbols: string[]) {
  return symbols.length < 1 || symbols.length > 5 || new Set(symbols).size !== symbols.length || symbols.some(symbol => !/^[A-Z0-9]{2,12}$/.test(symbol))
    ? 'Indica entre uno y cinco símbolos distintos, de 2 a 12 letras o cifras, separados por comas.' : null
}
class ResponseError extends Error {
  readonly status: number
  constructor(status: number) { super('No se pudo completar la solicitud'); this.status = status }
}
async function readJson<T>(url: string, signal: AbortSignal, options?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...options, signal: AbortSignal.any([signal, AbortSignal.timeout(20_000)]) })
  if (!response.ok) throw new ResponseError(response.status)
  return response.json() as Promise<T>
}

function DesktopRole({ id, name, purpose, health, stale }: { id: string; name: string; purpose: string; health: TeamHealth | null; stale: boolean }) {
  const challenges = health?.integration.challenges?.filter(row => row.agent === id) ?? []
  const now = Date.now() / 1000
  const fresh = challenges.filter(row => row.state === 'receipt_verified' && typeof row.createdAt === 'number' && typeof row.observedAt === 'number' && now - row.createdAt <= 3600 && now - row.observedAt <= 3600 && now - row.createdAt >= -120 && now - row.observedAt >= -120)
  const latest = [...fresh].sort((a, b) => (b.observedAt ?? 0) - (a.observedAt ?? 0))[0]
  const invalid = challenges.some(row => row.state === 'blocked' || (row.createdAt !== undefined && now - row.createdAt > 3600) || (row.observedAt !== undefined && now - row.observedAt > 3600))
  const pending = challenges.filter(row => row.state !== 'receipt_verified').length
  const label = stale ? 'Lectura pendiente' : latest ? 'Acuse local vigente' : invalid ? 'Prueba inválida o caducada' : 'Acuse pendiente'
  return <article className={`professional-role ${latest && !stale ? 'has-receipt' : ''}`}>
    <span className="professional-role-number" aria-hidden="true">{ROLES.findIndex(row => row[0] === id) + 1}</span>
    <h4>{name}</h4><p>{purpose}</p><span className="professional-role-state">{label}</span>
    <small>{latest ? `Acuse: ${stamp(latest.observedAt)}` : 'Sin acuse vigente comprobado'}</small>
    {latest && pending > 0 && <small>{pending} pruebas adicionales pendientes o inválidas.</small>}
    <small>{health?.integration.localProfiles?.includes(id) ? 'También existe una función local de validación.' : 'El perfil local describe instrucciones.'}</small>
  </article>
}

function TaskDetails({ task }: { task: Task }) {
  const stages = task.stages ?? []
  const blockers = task.proposal?.blockers ?? stages.flatMap(stage => stage.result?.blockers ?? [])
  const uniqueBlockers = [...new Set(task.error ? [...blockers, task.error] : blockers)]
  const verifier = [...stages].reverse().find(stage => stage.stage === 'verificador')?.result
  return <article className="professional-task-detail" aria-label="Detalle del encargo seleccionado">
    <div className="professional-row"><strong>{statusLabel(task.status)}</strong><time>{stamp(task.updatedAt)}</time></div>
    <p className="professional-id">Encargo {task.taskId}</p>
    {task.correlationId && <p className="professional-id">Correlación {task.correlationId}</p>}
    {!!task.symbols?.length && <p>Universo: {task.symbols.join(', ')}</p>}
    {uniqueBlockers.length > 0 && <ul className="professional-blockers">{uniqueBlockers.map(blocker => <li key={blocker}>{BLOCKERS[blocker] ?? 'Un requisito del flujo sigue pendiente; revisar el contrato local.'}</li>)}</ul>}
    {stages.length > 0 && <ol className="professional-stage-list">{stages.map((stage, index) => <li key={`${stage.stage}:${stage.attempt}:${index}`}><strong>{({ encargo: 'Encargo', radar: 'Radar', verificador: 'Verificación', investigador: 'Investigación', revisor: 'Revisión', coordinador: 'Coordinación', propuesta: 'Propuesta' } as Record<string, string>)[stage.stage] ?? 'Etapa local'}</strong><span>{statusLabel(stage.status)}</span><small>Intento {stage.attempt} · {stage.actor?.kind === 'radar-adapter' ? 'Adaptador del radar' : 'Función local'} · {stamp(stage.observedAt)}</small>{!!stage.result?.missingSymbols?.length && <small>Sin caché vigente: {stage.result.missingSymbols.join(', ')}</small>}</li>)}</ol>}
    {verifier && <p className="professional-note">Fuentes: {verifier.claimedUrls ?? 0} URL declaradas · {verifier.retrievedUrls ?? 0} recuperadas. La captura y su huella no acreditan la veracidad de las afirmaciones ni la independencia editorial.</p>}
    <p className="professional-note">Completar etapas acredita un contrato local. El experimento y la regresión requieren evidencia de ejecución separada. Este flujo no autoriza operaciones ni acredita rentabilidad.</p>
  </article>
}

function ExperimentDetails({ experiment }: { experiment: Experiment }) {
  const evidence = experiment.latestEvidence
  const promotion = evidence?.kind === 'promotion_decision' ? evidence.payload : evidence?.payload.promotion
  const resultLabel = promotion?.status ? statusLabel(promotion.status) : evidence?.payload.status ? statusLabel(evidence.payload.status) : evidence ? 'Resultado descriptivo registrado' : 'Sin resultado económico publicado'
  return <article>
    <div className="professional-row"><strong>{experiment.plan_id ?? experiment.planId ?? 'Experimento'} · {experiment.manifest.symbols?.join(', ') ?? 'Universo registrado'}</strong><time>{stamp(experiment.frozen_at ?? experiment.frozenAt)}</time></div>
    <p className="professional-id">{experiment.id}</p>
    <dl><div><dt>Ensayo congelado</dt><dd>{experiment.events ?? '—'} eventos{experiment.manifest.benchmark ? ` · Referencia ${experiment.manifest.benchmark}` : ''}</dd></div><div><dt>Validación registrada</dt><dd>{resultLabel}</dd></div><div><dt>Observación futura</dt><dd>{promotion?.independentFuture === true ? 'Observaciones posteriores registradas' : 'Evidencia independiente pendiente'}</dd></div><div><dt>Costes / equivalencia</dt><dd>{experiment.manifest.costModel?.verified ? 'Costes verificados' : 'Costes de escenario'} · {promotion?.executionEquivalent === true ? 'Equivalencia verificada' : 'Equivalencia pendiente'}</dd></div></dl>
    {evidence && <p className="professional-note">Última evidencia: {stamp(evidence.observedAt)}{typeof evidence.payload.metrics?.trades === 'number' ? ` · ${numberOf(evidence.payload.metrics.trades)} operaciones` : typeof evidence.payload.samples === 'number' ? ` · ${numberOf(evidence.payload.samples)} parejas observadas` : ''}. Un resultado descriptivo no autoriza promoción.</p>}
    {promotion?.holdoutReused && <p className="professional-alert">El tramo reservado solapa observaciones ya consultadas; no aporta evidencia independiente.</p>}
  </article>
}

export default function ProfessionalPanel() {
  const [health, setHealth] = useState<TeamHealth | null>(null)
  const [catalog, setCatalog] = useState<PlanCatalog | null>(null)
  const [professional, setProfessional] = useState<ProfessionalStatus | null>(null)
  const [errors, setErrors] = useState({ health: false, catalog: false, professional: false })
  const [reload, setReload] = useState(0)
  const [busy, setBusy] = useState<'task' | 'plan' | null>(null)
  const [notice, setNotice] = useState('')
  const [formError, setFormError] = useState('')
  const [selectedTask, setSelectedTask] = useState('')
  const [task, setTask] = useState<Task | null>(null)
  const [taskError, setTaskError] = useState(false)
  const [brief, setBrief] = useState('Revisar la hipótesis de tendencia con costes netos y señalar la evidencia pendiente.')
  const [taskSymbols, setTaskSymbols] = useState('BTC')
  const [statement, setStatement] = useState('La expectancyNet de tendencia en 4h debe superar cero tras comisiones, spread y deslizamiento.')
  const [horizon, setHorizon] = useState('4h')
  const [sample, setSample] = useState(50)
  const [planId, setPlanId] = useState('A1')
  const [planSymbols, setPlanSymbols] = useState('BTC, ETH')
  const [benchmark, setBenchmark] = useState('BTC')
  const mutations = useRef(new Set<AbortController>())
  const lifecycle = useRef({ generation: 0, mounted: false })
  const submitting = useRef(false)
  const retry = useRef<{ body: string; key: string } | null>(null)

  useEffect(() => {
    const controllers = mutations.current
    const session = lifecycle.current
    session.mounted = true
    session.generation++
    return () => { session.mounted = false; session.generation++; controllers.forEach(controller => controller.abort()); controllers.clear() }
  }, [])

  useEffect(() => {
    if (busy) return
    let active = true
    let timer: number | undefined
    const controller = new AbortController()
    const poll = async () => {
      const result = await Promise.allSettled([
        readJson<TeamHealth>('/api/team/health', controller.signal),
        readJson<PlanCatalog>('/api/research/plans', controller.signal),
        readJson<ProfessionalStatus>('/api/professional/status', controller.signal),
      ])
      if (!active) return
      const [team, plans, status] = result
      if (team.status === 'fulfilled') setHealth(team.value)
      if (plans.status === 'fulfilled') setCatalog(plans.value)
      if (status.status === 'fulfilled') setProfessional(status.value)
      setErrors({ health: team.status === 'rejected', catalog: plans.status === 'rejected', professional: status.status === 'rejected' })
      timer = window.setTimeout(() => { void poll() }, 15_000)
    }
    void poll()
    return () => { active = false; controller.abort(); window.clearTimeout(timer) }
  }, [busy, reload])

  useEffect(() => {
    if (!selectedTask || busy) return
    let active = true
    let timer: number | undefined
    const controller = new AbortController()
    const poll = async () => {
      try {
        const next = await readJson<Task>(`/api/team/tasks/${encodeURIComponent(selectedTask)}`, controller.signal)
        if (active && next.taskId === selectedTask) {
          setTask(previous => previous?.taskId === next.taskId && (previous.updatedAt ?? 0) > (next.updatedAt ?? 0) ? previous : next)
          setTaskError(false)
        }
      } catch { if (active) setTaskError(true) }
      if (active) timer = window.setTimeout(() => { void poll() }, 15_000)
    }
    void poll()
    return () => { active = false; controller.abort(); window.clearTimeout(timer) }
  }, [selectedTask, busy, reload])

  const submit = async (event: FormEvent<HTMLFormElement>, kind: 'task' | 'plan') => {
    event.preventDefault()
    if (submitting.current) return
    const symbols = symbolsOf(kind === 'task' ? taskSymbols : planSymbols)
    const validation = symbolError(symbols)
    if (validation) { setFormError(validation); return }
    if (kind === 'task' && (brief.trim().length < 8 || brief.trim().length > 1000 || statement.trim().length < 24 || statement.trim().length > 240 || !statement.includes('expectancyNet') || !Number.isInteger(sample) || sample < 30 || sample > 500)) {
      setFormError('El encargo necesita 8–1.000 caracteres; la hipótesis, 24–240 e incluir expectancyNet; la muestra, un entero entre 30 y 500.'); return
    }
    if (kind === 'plan' && (!catalog?.plans.some(plan => plan.id === planId) || (planId === 'A3' && (symbols.length < 2 || !symbols.includes(benchmark))))) {
      setFormError('A3 necesita al menos dos símbolos y una referencia incluida en ese universo. Elige un plan disponible.'); return
    }
    submitting.current = true
    const session = lifecycle.current
    const generation = session.generation
    const controller = new AbortController()
    mutations.current.add(controller)
    setBusy(kind); setNotice(''); setFormError('')
    try {
      if (kind === 'task') {
        const body = { brief: brief.trim(), symbols, radarMode: 'cached', requireDesktopReceipt: false, hypothesis: { statement: statement.trim(), metric: 'expectancyNet', universe: 'trend', horizon, direction: 'long', minSample: sample } }
        const serialized = JSON.stringify(body)
        if (retry.current?.body !== serialized) retry.current = { body: serialized, key: `panel-${crypto.randomUUID()}` }
        const next = await readJson<Task>('/api/team/tasks', controller.signal, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...body, idempotencyKey: retry.current.key }) })
        if (!session.mounted || generation !== session.generation) return
        if (next.taskId) { setTask(next); setSelectedTask(next.taskId); setTaskError(false); setNotice(`Encargo registrado: ${statusLabel(next.status).toLowerCase()}.`) }
        else setNotice(BLOCKERS[next.error ?? ''] ?? 'El encargo no se ha creado. Revisa el estado del servicio.')
      } else {
        await readJson<Experiment>(`/api/research/plans/${encodeURIComponent(planId)}/preregister`, controller.signal, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbols, benchmark: planId === 'A3' ? benchmark : null }) })
        if (!session.mounted || generation !== session.generation) return
        setNotice(`Plan ${planId} registrado con universo y parámetros congelados. La evaluación sigue pendiente.`)
      }
    } catch (error) {
      if (session.mounted && generation === session.generation) setFormError(error instanceof ResponseError && error.status === 409 ? 'El servicio rechazó una solicitud en conflicto. Consulta el estado antes de repetir.' : kind === 'task' ? 'No se pudo confirmar el encargo. Repetir el mismo formulario reutiliza su clave y evita duplicarlo.' : 'No se pudo confirmar el registro. Actualiza la lista de experimentos antes de repetir para evitar otro registro.')
    } finally {
      if (session.mounted && generation === session.generation) { setBusy(null); setReload(value => value + 1) }
      submitting.current = false
      mutations.current.delete(controller)
    }
  }

  const selectedPlan = catalog?.plans.find(plan => plan.id === planId)
  const registered = catalog?.experiments ?? []
  const recent = health?.recentTasks ?? []
  const services = professional?.progress?.services ?? []
  const cryptoServices = services.filter(service => service.source === 'desk' && service.id === 'crypto')
  const taskIsCurrent = task?.taskId === selectedTask
  return <section className="professional-panel" aria-labelledby="professional-title">
    <header className="professional-heading"><span className="professional-mark" aria-hidden="true">◎</span><div><span className="professional-eyebrow">EVIDENCIA ANTES DE DECIDIR</span><h2 id="professional-title">Mesa de investigación</h2><p>Cripto spot · encargos acotados, planes congelados y seguimiento de pruebas.</p></div><span className="professional-badge">Paper / demo</span></header>
    <p className="professional-scope">La entrega de un agente y un resultado histórico no acreditan rentabilidad. Registrar o revisar un plan no activa estrategias ni abre operaciones.</p>
    <details className="professional-section" open>
      <summary><span><strong>Equipo y conexión</strong><small>Cinco roles de escritorio, transporte y funciones locales</small></span><span aria-hidden="true">⌄</span></summary>
      <div className="professional-section-body">
        {errors.health && <p className="professional-alert" role="status">El equipo está pendiente de actualizar. Se conserva la última lectura; los acuses no se presentan como actuales.</p>}
        <div className="professional-roles">{ROLES.map(([id, name, purpose]) => <DesktopRole key={id} id={id} name={name} purpose={purpose} health={health} stale={!health || errors.health} />)}</div>
        <p className="professional-note">Un acuse comprueba localmente el artefacto y los identificadores de su encargo. Caduca en una hora; no es una identidad firmada por el proveedor ni una conexión permanente. Las tareas que exijan escritorio necesitan sus propios acuses.</p>
        <div className="professional-transport"><div><h3>Transporte de suscripción</h3><p>Presencia del cliente y respuestas anteriores. Esta lectura no inicia clientes ni comprueba sesión o cuota actual.</p></div><ul>{(health?.provider?.providers ?? []).map(provider => <li key={provider.provider}><strong>{provider.provider === 'xai' ? 'Grok' : provider.provider === 'openai' ? 'Codex' : 'Proveedor'}</strong><span>{provider.installed ? 'Cliente instalado' : 'Cliente no detectado'} · {statusLabel(provider.state)}</span><small>Última respuesta correcta: {stamp(provider.lastSuccessAt)}{errors.health ? ' · Lectura pendiente' : ''}</small></li>)}</ul>{!health?.provider?.providers?.length && <p>Transporte pendiente de lectura.</p>}</div>
        <p className="professional-note">Las etapas del flujo usan funciones locales y un adaptador de radar. Los archivos de perfiles son instrucciones; completar una función local no demuestra que un bot de escritorio haya recibido el encargo.</p>
        {health && <p className="professional-note">Lectura del equipo: {stamp(health.observedAt)} · Europe/Madrid · {health.receipts ?? 0} recibos de etapas locales; {health.failedReceipts ?? 0} con error, demora o cancelación.</p>}
      </div>
    </details>
    <div className="professional-work-grid">
      <details className="professional-section">
        <summary><span><strong>Revisión con caché</strong><small>Sin nueva consulta a un proveedor</small></span><span aria-hidden="true">⌄</span></summary>
        <div className="professional-section-body"><form onSubmit={event => { void submit(event, 'task') }}>
          <fieldset disabled={busy !== null}><legend>Preparar un encargo de investigación</legend>
            <label>Encargo<textarea value={brief} onChange={event => setBrief(event.target.value)} minLength={8} maxLength={1000} required rows={3} /></label>
            <label>Símbolos<input value={taskSymbols} onChange={event => setTaskSymbols(event.target.value)} maxLength={70} required aria-describedby="professional-symbol-help" /></label>
            <p id="professional-symbol-help" className="professional-note">Entre uno y cinco, por ejemplo BTC, ETH. La falta de caché vigente deja el encargo bloqueado.</p>
            <label>Hipótesis medible<textarea value={statement} onChange={event => setStatement(event.target.value)} minLength={24} maxLength={240} required rows={3} /></label>
            <div className="professional-form-row"><label>Horizonte<select aria-label="Horizonte" value={horizon} onChange={event => setHorizon(event.target.value)}><option value="1h">1 hora</option><option value="4h">4 horas</option><option value="1d">1 día</option></select></label><label>Muestra mínima<input type="number" value={sample} onChange={event => setSample(event.target.valueAsNumber)} min={30} max={500} step={1} required /></label></div>
            <p className="professional-note">Métrica fija expectancyNet · universo de hipótesis: tendencia · posición larga. Es una propuesta sin experimento ejecutado. No se exige acuse de escritorio para esta revisión local.</p>
            <button type="submit">{busy === 'task' ? 'Revisando…' : 'Revisar caché local'}</button>
          </fieldset>
        </form><p className="professional-note">Máximo {health?.limits?.tasksPerDay ?? 4} encargos nuevos por día UTC. Repetir el mismo formulario recupera el mismo encargo; cambia el texto para preparar otro.</p></div>
      </details>
      <details className="professional-section">
        <summary><span><strong>Planes A1–A7</strong><small>Congelar hipótesis, universo y referencia</small></span><span aria-hidden="true">⌄</span></summary>
        <div className="professional-section-body"><form onSubmit={event => { void submit(event, 'plan') }}>
          <fieldset disabled={busy !== null || !catalog || errors.catalog}><legend>Registrar antes de evaluar</legend>
            <label>Plan<select aria-label="Plan" value={planId} onChange={event => setPlanId(event.target.value)}>{(catalog?.plans ?? []).map(plan => <option key={plan.id} value={plan.id}>{plan.id} · {plan.name}</option>)}</select></label>
            {selectedPlan && <p className="professional-plan-description">{selectedPlan.hypothesis}{selectedPlan.interval ? ` · Velas de ${selectedPlan.interval}.` : ' · Requiere observaciones emparejadas disponibles al decidir.'}</p>}
            <label>Universo congelado<input value={planSymbols} onChange={event => setPlanSymbols(event.target.value)} maxLength={70} required /></label>
            {planId === 'A3' && <label>Referencia de fuerza relativa<select aria-label="Referencia de fuerza relativa" value={symbolsOf(planSymbols).includes(benchmark) ? benchmark : ''} onChange={event => setBenchmark(event.target.value)} required><option value="" disabled>Elige un símbolo del universo</option>{[...new Set(symbolsOf(planSymbols))].map(symbol => <option key={symbol} value={symbol}>{symbol}</option>)}</select><small>A3 necesita dos o más símbolos e incluye la referencia en el mismo universo.</small></label>}
            <p className="professional-note">Se congelan parámetros y modelo de costes del servidor. Cada registro cuenta como un ensayo, aunque se repita el plan. A5–A7 esperan datos observados; no se inventan fills ni resultados.</p>
            <button type="submit">{busy === 'plan' ? 'Registrando…' : 'Registrar plan congelado'}</button>
          </fieldset>
        </form></div>
      </details>
    </div>
    <div className="professional-feedback" aria-live="polite" aria-atomic="true">{notice && <p className="professional-notice">{notice}</p>}{formError && <p className="professional-alert" role="alert">{formError}</p>}</div>
    <details className="professional-section" open={Boolean(selectedTask) || undefined}>
      <summary><span><strong>Encargos y bloqueos</strong><small>{recent.length} encargos recientes disponibles</small></span><span aria-hidden="true">⌄</span></summary>
      <div className="professional-section-body">
        {recent.length ? <div className="professional-task-list" aria-label="Seleccionar encargo">{recent.map(row => <button type="button" key={row.taskId} aria-pressed={row.taskId === selectedTask} onClick={() => { setSelectedTask(row.taskId); setTaskError(false) }}><strong>{statusLabel(row.status)}</strong><small>{stamp(row.updatedAt)}</small><span>{row.taskId}</span></button>)}</div> : <p className="professional-empty">{errors.health ? 'Lista pendiente de actualizar.' : health ? 'No hay encargos registrados.' : 'Consultando los encargos…'}</p>}
        {taskError && <p className="professional-alert" role="status">No se pudo actualizar este encargo. Los datos mostrados conservan su fecha anterior.</p>}
        {selectedTask && (taskIsCurrent && task ? <TaskDetails task={task} /> : <p className="professional-empty" role="status">{taskError ? 'El detalle del encargo no está disponible.' : 'Consultando el encargo seleccionado…'}</p>)}
      </div>
    </details>
    <details className="professional-section">
      <summary><span><strong>Registro y validación</strong><small>{catalog ? `${catalog.trials} ensayos acumulados · ${registered.length} registros visibles` : 'Consultando el registro'}</small></span><span aria-hidden="true">⌄</span></summary>
      <div className="professional-section-body">
        {errors.catalog && <p className="professional-alert" role="status">Registro pendiente de actualizar. Se conserva la última lectura y se pausa la creación de planes.</p>}
        {catalog && <p className={`professional-ledger ${catalog.ledger ? '' : 'is-blocked'}`}>{catalog.ledger ? 'Cadena del registro íntegra en esta lectura' : 'Integridad del registro no superada'} · Integridad y rentabilidad son comprobaciones distintas.</p>}
        <div className="professional-evidence-guide"><div><strong>Validación histórica</strong><p>Datos anteriores a la congelación: exploración. Superar filtros no añade evidencia futura independiente.</p></div><div><strong>Observación futura</strong><p>Exige datos nuevos posteriores a la congelación, sin solapamiento, costes verificados y equivalencia de ejecución.</p></div></div>
        {registered.length ? <div className="professional-experiments">{registered.map(experiment => <ExperimentDetails key={experiment.id} experiment={experiment} />)}</div> : <p className="professional-empty">{catalog ? 'Aún no hay planes registrados.' : 'Registro pendiente de lectura.'}</p>}
        <p className="professional-note">El número de eventos no mide resultados. Los registros visibles se limitan a los últimos 100; los ensayos acumulados incluyen el histórico. No hay promoción automática.</p>
      </div>
    </details>
    <footer className="professional-footer"><div><strong>Progreso observado de cripto</strong><p>{errors.professional ? 'Lectura pendiente; se conserva la fecha anterior.' : cryptoServices.length ? cryptoServices.map(service => `${service.role === 'risk' ? 'Vigilancia de riesgo' : 'Escaneo'}: ${service.state === 'progressing' ? 'progreso reciente' : service.state === 'stalled' ? 'sin progreso reciente' : service.state === 'warming' ? 'iniciando' : 'sin prueba de progreso'}`).join(' · ') : 'Sin una lectura de progreso disponible.'}</p>{professional?.progress?.observedAt && <small>{stamp(professional.progress.observedAt)} · Europe/Madrid</small>}</div><button type="button" disabled={busy !== null} onClick={() => setReload(value => value + 1)}>Actualizar lecturas</button></footer>
  </section>
}
