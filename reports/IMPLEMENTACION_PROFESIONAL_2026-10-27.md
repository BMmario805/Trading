# Implementación y verificación profesional — 27 de octubre de 2026

Se han implementado las correcciones de ejecución, riesgo, trazabilidad e investigación
derivadas de la auditoría. El servicio local sigue en paper/demo. La rentabilidad del
proyecto continúa sin demostrarse; no se han habilitado promociones automáticas,
reseteado libros ni aumentado los presupuestos de riesgo para esta validación.

## Cambios entregados

| Área | Comportamiento implementado |
|---|---|
| Ejecución | Comprobación final de señal, cotización, estado y cupo después de las esperas; identidad del candidato vinculada al quorum. |
| Pausa y cierres | Cancelación de compras maker pendientes conservando fills y salidas; cierres con cotización vigente y protección frente a una posición reemplazada. |
| MT5 | Reservas persistentes antes del envío, recuperación conservadora y tratamiento separado de rechazo y resultado incierto. |
| Riesgo conjunto | Instrumentos canónicos, exposición marcada y conversión vigente; entradas bloqueadas ante exposición o conversión desconocidas. |
| Datos | Fecha real cuando la fuente la ofrece; datos sin fecha no se presentan como ejecutables y recientes. Señales de derivados basadas en barras cerradas. |
| Evaluación | Motor 8, política de mesa v9 y separación de cohortes; costes normales, estrés de política y estrés del mismo conjunto de operaciones. |
| Selección | Registro acumulado de ensayos; la corrección estadística declara sus límites y no presume búsquedas históricas desconocidas. |
| Trazabilidad | Registro SQLite con cadena de hashes, entradas OHLCV archivadas y atribución candidata/decisión/resultado con reintentos de auditoría. |
| Equipo | Cinco roles de escritorio, perfiles locales identificados, flujo finito con presupuesto, identificadores y acuses que caducan. |
| Noticias | Hasta cuatro URL públicas permitidas por encargo; límites de tiempo/tamaño, hash, fecha y deduplicación. Recuperar una fuente no corrobora sus afirmaciones. |
| Operación | Panel de investigación, progreso de servicios, dependencias fijadas, CI preparado y copia/restauración verificables en staging. |

Los overlays de IA, derivados y noticias sin validación equivalente permanecen en
observación. Las probabilidades sin calibrar se presentan como puntuaciones.
Los fallos de escritura de atribución no impiden gestionar salidas.

## Validación ejecutada

- Backend: **614 pruebas y 158 subpruebas aprobadas; cinco omitidas** por permisos
  de creación de enlaces simbólicos en Windows. Informe `professional-tests-2026-10-27.xml`.
- Frontend: compilación de producción y lint aprobados. Persisten tres avisos
  anteriores de React y un aviso de tamaño del paquete; no son errores de compilación.
- Dependencias: `pip check` sin incompatibilidades.
- Copia: 34 archivos en `.runtime/professional-backups-20261027/snapshot-20261027T182711198996Z`.
  Huellas verificadas y restauración real en `.runtime/recovery-check-20261027`,
  sin activar esa copia ni sobrescribir los libros en uso. Las copias son coherentes
  por base de datos; no constituyen una transacción simultánea entre todas las bases.
- El servicio local se reinició con los cambios. La comprobación posterior respondió
  `ok`, tres mesas, siete bots y ningún servicio detenido según el monitor.
  Motor 8, política `desk-rs-chandelier-1h-v9`, `automaticPromotion=false`.
- CI de Windows/Linux y contenedor preparados; no se afirma una ejecución externa
  de CI ni una construcción Docker realizada en esta sesión.

## Siete experimentos registrados

Entradas: cuatro capturas públicas de Binance, BTC y ETH en 1d/4h, con 999 barras
cerradas por serie y huellas verificadas. Se reutilizaron las capturas del 26 de
octubre; no se buscaron parámetros adicionales ni se inventaron observaciones futuras.
La congelación del 27 de octubre ocurre después de las capturas: estos resultados
son exploración histórica, no seguimiento prospectivo.

| Plan | Resultado de esta ejecución |
|---|---|
| A1, ruptura diaria | Rechazado. BTC: 2 operaciones, PnL de referencia +1,91; ETH: 2 operaciones, −0,08. Muestra insuficiente. |
| A2, ruptura 4h | Rechazado. BTC: 2 operaciones, −2,00; ETH: 1 operación, −1,00. |
| A3, fuerza relativa | Bloqueado: el periodo reservado ya había sido consultado por A1. |
| A4, filtro ADX | Bloqueado: el periodo reservado ya había sido consultado por A2. |
| A5, maker/taker | Contrato de comparación congelado; cero parejas observadas, pendiente. |
| A6, reglas/IA | Contrato de comparación congelado; cero parejas observadas, pendiente. |
| A7, noticias | Contrato de comparación congelado; cero parejas observadas, pendiente. |

Cada serie usa capital de referencia independiente: esos PnL no se suman como una
rentabilidad de cartera y no son movimientos de las cuentas. Los contratos A5–A7
identifican los brazos del estudio; no equivalen a disponer ya de un colector automático
completo con contrafactuales observados. Deben registrarse predicciones y resultados
emparejados reales. El registro verificó su cadena de hashes tras la ejecución.

Resultados, parámetros y hashes: `professional-research-2026-10-27.json`;
procedencia: `research-inputs-2026-10-26.json`.

## Flujo Grok

Los cinco bots entregaron pruebas individuales el 26 de octubre. Se conservaron
como evidencia histórica y caducan al cabo de una hora; no acreditan conexión permanente
ni identidad firmada por el proveedor. El 27 de octubre se abrió la ventana real
de Grok Bot y se confirmó la presencia del equipo y sus conversaciones.

Después se renovó la prueba individual del **27 de octubre**: cada rol recibió
directamente su propio encargo, leyó su challenge y escribió su recibo. La
comprobación independiente de archivos confirmó **5/5 acuses válidos, sin roles
pendientes**, con hash del artefacto, nonce e identificadores coincidentes.
Evidencia: `grok-flow-verification-2026-10-27.json`. Esta prueba también caduca;
su resultado no se debe presentar como conexión permanente.

Los puentes por suscripción se probaron de nuevo el 27 de octubre: Grok respondió
correctamente en 7,84 segundos y Codex en 4,05 segundos, con esquema y marcador
aleatorio verificados, sin herramientas, órdenes ni fallback a API de pago.
Esas pruebas se ejecutaron en un proceso separado; no se falsifica el estado
observado por el proceso principal. Evidencia: `subscription-flow-probe-2026-10-27.json`.

Una tarea real por la API completó los 13 acuses de sus siete etapas. Terminó
bloqueada por radar vigente ausente, noticias sin verificar, hipótesis ausente y
acuses de escritorio caducados. Ese resultado verifica la ruta conservadora de
rechazo; no demuestra una propuesta de inversión lista. Evidencia:
`runtime-verification-2026-10-27.json`.

La tarea bloqueada se conserva como registro de lo sucedido antes de renovar
los acuses; no se reescribe su resultado a posteriori. La lectura final de salud
se guarda por separado dentro del mismo informe. La conversión estaba completa
y no había reservas pendientes. En una lectura anterior la exposición estaba
completa; una lectura posterior detectó una cotización MT5 de EURUSD de más de dos
minutos, por lo que `exposureComplete=false` y una posición queda marcada como
antigua/no cuantificable con precio vigente. La admisión de nuevas entradas se
bloquea hasta reconciliar esa cotización; no se retimó ni se inventó un precio.
En el seguimiento posterior la fuente volvió a actualizarse: exposición y conversión
completas, cero marcas antiguas, cero posiciones sin cuantificar y cero reservas
pendientes. Se conservan ambas lecturas en `riskFollowup` para no ocultar la incidencia.
El contador diagnóstico
`invalidReceipts` incluye comparaciones entre recibos de distintos roles: debe
interpretarse junto a los cinco acuses aceptados, no como cinco entregas fallidas.

Se conserva además una copia de 139 archivos de código, pruebas y dependencias,
sin credenciales ni libros, en `.runtime/professional-source-20261027.zip`, con
manifiesto de huellas `source-manifest-2026-10-27.json`. El hash del código de los
experimentos coincide con el código comprobado al terminar la ejecución.

## Trabajo que requiere evidencia nueva

1. Acumular observaciones futuras por cohorte y sin reutilizar periodos reservados.
2. Verificar comisiones, spreads, deslizamiento y condiciones de la cuenta real
   antes de afirmar equivalencia económica con producción.
3. Recoger las parejas de A5–A7, con entradas disponibles antes de la decisión,
   iguales presupuestos y políticas congeladas. No rellenar contrafactuales con ficción.
4. Corroborar afirmaciones e independencia editorial de noticias; las capturas son
   insumos de investigación, no señales suficientes de ejecución.
5. Demostrar estabilidad fuera de muestra y en seguimiento antes de plantear
   promoción manual. Ninguna duración o número de operaciones garantiza beneficio.

No se puede concluir con la evidencia actual que existan beneficios altos, rápidos
o sostenibles. La mejora entregada permite medir y rechazar estrategias con más rigor.

Nota operativa: una revisión automática anterior rechazó borrar un directorio temporal
de pruebas (`tmpc1f02r_n`). Se conservó; no impidió las pruebas ni la restauración.
