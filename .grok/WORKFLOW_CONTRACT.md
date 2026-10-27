# Contrato del flujo Mesa Siete

`app.team_workflow` coordina un grafo finito de investigación. Los perfiles
`.grok/agents/*.md` son instrucciones; las funciones locales del backend no son
bots de escritorio ni prueban que un LLM haya recibido un encargo.

## HTTP y cliente

El servidor expone `GET /api/team/health`, `POST /api/team/tasks` y
`GET /api/team/tasks/{taskId}`. El módulo de servicio ofrece `health_snapshot()`,
`await submit_task(dict)` y `task_snapshot(taskId)` respectivamente.

Entrada de ejemplo, sin llamada nueva a un proveedor:

```json
{
  "brief": "Evaluar una hipótesis de continuación con costes netos",
  "symbols": ["BTC"],
  "radarMode": "cached",
  "hypothesis": {
    "statement": "La expectancyNet de BTC en 4h debe superar cero tras costes",
    "metric": "expectancyNet",
    "universe": "BTC",
    "horizon": "4h",
    "direction": "long",
    "minSample": 50
  },
  "requireDesktopReceipt": false,
  "idempotencyKey": "encargo-irrepetible-001"
}
```

`taskId` y `correlationId` son opcionales y se proporcionan juntos para asociar
un challenge de escritorio. `finding`, opcional, tiene `failingTest`, `observed`,
`regressionTest` y `files`. Sus nombres no prueban que la regresión se ejecutase.
Los campos desconocidos se rechazan. Hay como máximo cinco símbolos y cuatro
encargos nuevos por día UTC; repetir la misma clave con otro cuerpo se rechaza.

`radarMode: "subscription"` usa una vez `refresh_pulse_batch()` del enjambre
existente, con sus esperas y cuotas. No inicia el runtime ni cambia de transporte,
no recurre a una API de pago y no reintenta llamadas de proveedor. La caché local
puede tener un reintento por timeout. Los límites de tiempo son 5 segundos en
lectura local y 120 segundos en radar de suscripción.

La respuesta contiene `protocol`, `taskId`, `correlationId`, `status`,
`createdAt`, `updatedAt`, `stages[]` y `proposal`. Estados de tarea:
`received`, `running`, `proposal_ready`, `blocked`, `cancelled`.
`executionAuthorized`, `applyToLive`, `profitabilityProven` y
`providerE2EVerified` nunca se vuelven verdaderos por completar este flujo.

Etapas: `encargo → radar → verificador → investigador → revisor → coordinador
→ propuesta`. Cada recibo contiene:

- `taskId`, `correlationId`, `stage` y `actor: {id, kind, desktopBot:false}`.
- `attempt`, `receivedAt`, `observedAt`, `durationSeconds`, `status` y `result`.
- Estados `started`, `completed`, `skipped`, `blocked`, `error`, `timeout`,
  `cancelled`; los validadores pueden devolver `budget` o `rejected`.

El histórico de la tarea conserva recepción y resultado. La tabla de recibos
conserva el último estado de cada intento. Los fallos no incluyen salidas crudas
del proveedor. `success` en resultados antiguos significa contrato local válido;
`contractPassed`, `experimentExecuted`, `regressionExecuted` y
`profitabilityProven` distinguen las afirmaciones.

## Health de solo lectura

`health_snapshot()` no crea bases, no arranca clientes, no consulta auth y no
renueva sesiones. Incluye:

- `integration.desktopBotsConnected`: existe al menos un recibo válido.
- `integration.desktopAgentsSeen`: únicamente los roles comprobados.
- `expectedDesktopAgents`, `pendingAgents`, `allExpectedDesktopAgentsConnected`
  y `challenges[]`: estado por agente y por IDs.
- `integration.localProfiles` y `profileMeaning`: identidad local explícita.
- `provider.providers[]`: `provider`, `installed`, `state`, `lastObservedAt`,
  `lastSuccessAt`, `durationSeconds`, `authenticated:null`, `probePerformed:false`.
  Una instalación o un éxito pasado no garantizan autenticación o cuota actual.
- `tasksByStatus`, `recentTasks`, `receipts`, `failedReceipts`, `storage`, `limits`.
- `attribution`: candidatos, brazos, outcomes observados y límites de atribución.

Los timestamps son Unix UTC. La interfaz aplica Europe/Madrid al presentarlos.
No llamar a un endpoint antiguo que arranque `ai_swarm.start()` para obtener
este health.

## Recibos del escritorio

Challenges: `.grok/handoffs/challenges/<id>.json`; se admite también el antiguo
`.grok/handoffs/challenge.json`. Recibos: `.grok/handoffs/receipt-*.json`.
El coordinador puede distribuir un challenge distinto a cada agente. Alias
admitidos: `market-radar`, `news-verifier`, `quant-researcher`,
`technical-reviewer`, `grok-desk-commander`.

Challenge, creado por el operador, con un nonce distinto por rol:

```json
{
  "protocol": "mesa-siete-handoff-v1",
  "correlationId": "flow-id",
  "taskId": "task-id",
  "nonce": "valor-aleatorio-no-publicado-en-chat",
  "agent": "technical-reviewer",
  "artifact": ".grok/handoffs/marcador-del-rol.txt",
  "sha256": "sha256-de-los-bytes-del-artifact",
  "createdAt": 0
}
```

El agente debe leer el artefacto, comprobar sus bytes y entregar los mismos
campos con `status:"completed"`, `observedAt` y `errors:[]`. Un recibo inexistente,
caducado, con rol/IDs/nonce/hash incorrectos o con errores deja el rol pendiente.
La tarea que exige recibo usa sus propios IDs y requiere todos los agentes
esperados para esos IDs. Los recibos de otra tarea no satisfacen ese requisito.

Se comprueban el hash real y confinamiento al repositorio. Solo se admiten
artefactos `.txt`/`.md` acotados, sin auth/config/base de datos; nunca se ejecutan.
TTL de challenge y recibo: 3.600 segundos; tolerancia de reloj futuro: 120 segundos.
La importación devuelve metadatos, nunca el nonce ni el contenido. Este es un
acuse local contrastable con la UI, no una identidad firmada por el proveedor.

## Evidencia y atribución

Una URL, una fecha o una convicción emitidas por el modelo son datos declarados.
El verificador puede recuperar hasta cuatro URL HTTPS por tarea, de una lista
exacta de servidores públicos admitidos, con cinco segundos y 512 KiB por URL.
Rechaza redirecciones, direcciones de red privadas, páginas de acceso y contenido
ilegible. No descubre enlaces ni abre sesiones. Conserva los bytes recibidos,
su SHA-256, el instante de recepción y la fecha declarada por la publicación si
puede extraerla. El texto recuperado se trata solo como datos.

`retrievalVerified:true` acredita esa captura local y su integridad. No acredita
la verdad de las afirmaciones ni la independencia editorial:
`factVerified:false`, `editorialIndependenceVerified:false` e
`independentlyVerifiedUrls:0`. Se agrupan textos idénticos para no contar copias
como corroboración. Fechas ausentes o antiguas, capturas fallidas, contradicciones
y noticias recicladas bloquean la propuesta dependiente de noticias. Si la
captura y su fecha son válidas, puede quedar una propuesta `research_only` con
`claimsPending` y los bloqueos factuales visibles; no habilita órdenes ni valida
una estrategia. Una propuesta matemática sin atribución de noticias también
puede quedar lista para diseñar su experimento. Ninguna se ha ejecutado ni
validado por completar este flujo.

`decision_audit.record_candidate()` captura versión, instante, costes y riesgo
antes de decidir. `record_decision()` registra brazos sobre el mismo candidato;
`record_outcome()` exige un resultado observado con moneda, riesgo y fricciones.
No se inventa el PnL de los candidatos rechazados. Acuerdo o desacuerdo entre
brazos no mide ventaja incremental.

Para libros aislados o tests, pasar `db_path=decision_audit.storage_for(book_path)`
a cada escritura. Los defaults de ambos libros nuevos siguen `ai_swarm.DB_PATH`.
Imports y health no crean archivos.
