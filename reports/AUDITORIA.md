# Auditoría del motor de trading — 9 de septiembre de 2026

## Perfil temporal de mayor frecuencia — 15 de octubre de 2026

Scalper y Squeeze usan un umbral base de 0,15 y hasta seis posiciones totales.
Los parámetros entrenados o ajustados manualmente por mercado siguen teniendo
prioridad. Se conservan el riesgo objetivo por entrada, el límite agregado del
6%, los frenos diarios, los vetos y el máximo de cinco posiciones a prueba.
Flujo mantiene su configuración propia. MT5 conserva su límite compartido.

En Mesa Siete, Tape admite medias separadas hasta 0,5 ATR en sentido adverso y
amplía la zona de descuento. Price admite retroceso o impulso ascendente reciente,
RSI de 15 a 80 y extensión entre -4 y +2,25 ATR, conservando el máximo de 2 ATR
entre la cotización y el cierre de señal. La mesa sigue siendo spot simulado,
con una posición simultánea, exposición máxima de un tercio y riesgo objetivo
del 1%, comisión, deslizamiento, Wire y los demás controles existentes.

Verificación: 147 pruebas pasan, incluidas nuevas entradas de rebote e impulso
y rechazos por RSI extremo, extensión, desviación de precio y falta de setup.
Se actualizó una prueba antigua de salida que ya fallaba en HEAD: el código
exige pérdida de estructura bajo EMA50 menos 2 ATR, no una caída menor. La
regla de salida se conserva y ahora se prueban ambos casos explícitamente.
Estos ajustes amplían las condiciones de entrada; no demuestran mayor rentabilidad.

## Reloj MT5, recarga y umbrales manuales — 15 de octubre de 2026

La estimación del reloj selecciona los símbolos del terminal y usa el mayor
desfase válido entre sus ticks. Un primer tick atrasado ya no hace aparecer
las cotizaciones recientes de otras divisas como futuras. Se mantiene el filtro
de antigüedad: una cotización caducada sigue sin permitir entradas.

`start.ps1` recarga el backend mediante `start-background.ps1 -RestartBackend`.
El arranque identifica los procesos Python de este proyecto antes de sustituirlos,
conserva el frontend activo y comprueba que la API responde. Sin ese argumento,
`start-background.ps1` conserva los servicios existentes. Las llamadas actuales
a `meta_features()` ya eran compatibles; no se revirtieron archivos funcionales.
La regla de cruce reciente sigue vigente.

Tras copiar ambas bases a `.runtime/backups/clock-fix-20261015-100102/`, se
reiniciaron siete modelos y siete políticas, se activaron los siete bots y se
limpiaron tres estados de cooldown/día de MT5. No había órdenes inciertas.
Los siete pares aprobados con umbral superior a 0,40 se ajustaron a 0,35:
Reversión/AAPL, Swing/ADA y CL, Apertura/MSFT y Tendencia/BCH, ETH y LTC.
Los parámetros anteriores quedan registrados en los metadatos del ajuste.

Cambiar el umbral invalida la validación de esa configuración: estos ajustes
se cargan aparte de los parámetros validados y operan a prueba con el límite
actual del 35%. Se conservan SL, TP, trailing y procedencia. Un entrenamiento
posterior sustituye el ajuste y determina de nuevo su elegibilidad.

Verificación: 140 pruebas Python y cuatro escenarios PowerShell de arranque
con procesos simulados pasan. Se reinició el backend y se comprobó la API.

## Ajuste de exploración y diagnóstico — 15 de octubre de 2026

Por petición del usuario se ajusta el modo a prueba al 35% del riesgo habitual,
sin recargo de umbral, con probabilidad mínima del 30% si el metamodelo es maduro
y hasta cinco posiciones a prueba por bot. La puerta general del metamodelo
queda en el 25%. Se conservan los demás límites de riesgo; MT5 sigue limitado
por su configuración compartida de tres posiciones y por la cuenta DEMO autorizada.
Su riesgo objetivo por entrada sin validar pasa del 0,025% al 0,035%.

El diagnóstico muestra la probabilidad y el umbral cuando el metamodelo veta,
el umbral de señal cuando no se alcanza y la extensión cuando se descarta
perseguir el precio. Cuando la cuenta MT5 está llena se actualizan los motivos
de todos los mercados, en lugar de conservar mensajes de ciclos anteriores.

La inspección previa encontró cero muestras en los siete modelos y ninguna orden
`pending` o `uncertain`. `strategy.py` y `market_data.py` coincidían con HEAD:
no existían las funciones `_orthogonal_trend_blend` o `_mt5_candles` citadas en
el adjunto. Se verificó que XAUUSD llegaba desde MT5 con una vela de 79 segundos
de antigüedad; las pruebas con relojes adelantados dos y tres horas conservan
las últimas velas cerradas al normalizar a UTC. No se cambió la escala de señal.

Con el backend detenido se copiaron ambas bases a
`.runtime/backups/unblock-20261015-093752/`, se limpiaron siete modelos y siete
políticas, se dejaron los siete bots en ejecución y se eliminaron ocho estados
MT5 de cooldown/día. No había órdenes inciertas que eliminar. Se conservan el
historial, las posiciones, los parámetros entrenados y los registros de órdenes
aceptadas. El aprendizaje puede reconstruir las políticas a partir del historial.

Verificación: 134 pruebas pasan. El backend se reinició para activar los cambios.
Las secciones siguientes documentan las revisiones anteriores.

## Modo a prueba restaurado — 15 de octubre de 2026

Por petición del usuario, los siete bots del Laboratorio y MT5 DEMO permiten
entradas sin validación vigente en modo a prueba. Se usa como máximo el 25%
del riesgo habitual, un umbral un 5% más exigente y hasta tres posiciones
a prueba por bot. También los símbolos sin entrenamiento entran en este modo.
Los parámetros aprobados siguen operándose con su umbral validado.

Se mantienen los vetos por pérdidas del Laboratorio, el filtro del metamodelo,
las cotizaciones recientes, los stops y los límites de cartera y pérdida diaria.
Los multiplicadores aprendidos no pueden elevar el riesgo a prueba por encima
del 25% del presupuesto habitual. Los modelos siguen excluyendo sentimiento.

MT5 conserva el bloqueo a cuentas que no sean la DEMO autorizada. Con el riesgo
base configurado del 0,1%, una entrada sin validar tiene un presupuesto máximo
del 0,025%, sujeto a la reserva de fricción y al redondeo del lote hacia abajo.
Se respeta además el límite compartido actual de tres posiciones en toda la cuenta.
La condición a prueba se guarda y se identifica también en el comentario MT5
para conservar el recuento tras reinicios. El panel muestra ambos riesgos.

Verificación: 128 pruebas pasan, incluyendo la apertura a prueba, los límites de
tamaño y posiciones, los vetos, la persistencia y el bloqueo de cuentas reales.
La web compila. Se reinició el backend y se verificó que MT5 seguía conectado
a la DEMO autorizada, con entradas activas y riesgo a prueba del 0,025%.
Durante la comprobación se ejecutaron dos entradas DEMO (Scalper/USDJPY y
Squeeze/GBPUSD) y el Laboratorio volvió a abrir posiciones simuladas.
Habilitar la exploración no fuerza entradas: todavía se requieren una señal
reciente y el cumplimiento de los demás filtros.

## Primera revisión de correcciones — 15 de octubre de 2026

El diagnóstico original de abajo describe el estado del 9 de septiembre.
En la primera revisión se verificaron o completaron estas correcciones
(el bloqueo estricto se sustituyó después por el modo a prueba descrito arriba):

- Los siete bots exigen validación vigente. Tanto los mercados rechazados
  como los pendientes de validar quedan bloqueados para nuevas entradas.
- Una optimización completa sin ventaja conserva `ok=False`, revoca los
  parámetros previos y espera el ciclo habitual del bot (4–12 horas).
  Los errores de fuente o histórico insuficiente reintentan tras una hora,
  también cuando quedan parámetros válidos de un entrenamiento anterior.
- La ventana de aprendizaje se ancla al último cierre con PnL del mismo bot.
  Al cargar se recalculan las políticas para recuperar estadísticas y vetos
  aunque la política persistida se hubiera quedado sin muestra durante el apagado.
- El metamodelo excluye «Opinión pública», «Señal combinada» y «Contexto macro
  oficial». Su versión de variables fuerza la reconstrucción de modelos antiguos
  usando snapshots compatibles y filtrados, sin invalidar parámetros de ejecución.
- El simulador ya aplica el filtro direccional de sobreextensión en todos los
  tramos, incluidos subperiodos y costes duplicados; el motor vigente es la versión 3.
- El histórico de sentimiento usa la conexión común con WAL, espera de bloqueo
  y cierre al terminar. La base local y `bodycam-mods/` ya estaban fuera del índice
  actual de Git; se amplían las exclusiones a diarios y variantes de SQLite.

Verificación: 113 pruebas pasan, incluidas 12 nuevas con bases temporales para
reintentos, recuperación tras inactividad, reconstrucción del metamodelo y SQLite.
No se ha modificado la base del usuario ni reiniciado el servicio. Los cambios
se activan al reiniciar el backend. Esta revisión no reevalúa la rentabilidad
ni actualiza las cifras históricas que siguen a continuación.

El proyecto ejecuta paper trading: las operaciones y el dinero son simulados.
Se mejoraron los seis bots existentes, sin añadir estrategias que no cuentan con validación.
Las pruebas de esta revisión no demuestran rentabilidad futura.

## Diagnóstico del historial anterior

Lectura de SQLite sin modificar cuentas, operaciones ni parámetros guardados:

| Bot | Operaciones cerradas | PnL simulado acumulado |
|---|---:|---:|
| Scalper | 292 | -132.72 |
| Swing | 84 | +1234.48 |
| Tendencia | 20 | -177.51 |
| Reversión | 10 | -601.30 |
| Apertura | 22 | +264.54 |
| Squeeze | 30 | +612.67 |

Estas cifras proceden del motor anterior, con defectos de simulación. No se recalculó
el historial ni se presentaron estas ganancias como evidencia de una ventaja real.

## Cambios implementados

- Señales sobre velas cerradas, ordenadas y sin duplicados ni OHLC inválidos.
- Cruce direccional de tres velas compartido: una inversión fuerte de largo a corto
  ya no queda bloqueada por comparar valores absolutos.
- El sentimiento continúa visible, pero no altera las entradas: el entrenamiento
  carece de sentimiento histórico causal equivalente.
- Los umbrales validados se respetan, sin descuentos posteriores por foco u hora.
- Antes de entrar se solicita una cotización reciente. Se descartan desviaciones
  de más de 1 ATR respecto al cierre de señal. Yahoo conserva la hora de origen.
- Stops en vivo al precio observado, y en histórico con salto adverso de apertura.
  El backtest ejecuta en la apertura siguiente y liquida posiciones al final del tramo.
- El tamaño incluye fricción estimada al stop, usa equity para margen y limita el
  riesgo inicial agregado al 6% de equity por bot. No equivale a un límite garantizado
  de pérdida: los gaps pueden superar el stop.
- El freno diario también considera pérdidas flotantes; se reevalúa después de cierres.
- Pausar nuevas entradas mantiene la vigilancia de posiciones abiertas.
- Distancia inicial de riesgo y trailing se guardan por operación. El aprendizaje
  usa el riesgo inicial, no el stop que se ha movido. Balance y cierre se guardan
  en una misma transacción; los cierres tras medianoche cuentan en el nuevo día.
- Conexiones SQLite cerradas al terminar cada transacción.
- Diagnóstico por mercado desplegable en cada tarjeta: señales, datos antiguos,
  costes, cooldown, exposición y entradas simuladas.
- Validación con al menos ocho operaciones, dos subperiodos positivos (mínimo dos
  operaciones por subperiodo) y resultado positivo con costes duplicados y ocho operaciones.
  Son filtros heurísticos; no constituyen una prueba de significación estadística.
- Los parámetros del motor antiguo dejan de contarse como validados hasta reentrenar.
  Un entrenamiento sin configuración rentable revoca el resultado previo. El histórico
  proxy del oro es informativo y no valida XAUUSD spot.

## Evaluación con históricos descargados

Se optimizó en el 70% inicial y evaluó en el 30% posterior, con embargo de 30 velas.
El tamaño por operación es fijo y depende del bot. Los importes no son comparables
como ranking de estrategias: cambian intervalos, ventanas, riesgo y mercados.
Se ejecutó una combinación por bot, no el universo completo.

| Bot | Mercado | Velas | Operaciones de validación | PnL de validación | Aceptado |
|---|---|---:|---:|---:|---|
| meanrev | BTC | 8759 | 120 | -2258.09 | No |
| scalper | BTC | 30239 | 4 | +95.77 | No |
| squeeze | BTC | 8639 | 41 | -1042.41 | No |
| swing | BTC | 8759 | 85 | -3367.56 | No |
| trend | BTC | 8759 | 63 | -2621.25 | No |
| orb | SPY | 4681 | 17 | -1417.66 | No |

Los JSON adyacentes contienen parámetros, tramos, costes duplicados, fuente y fechas.
`applied` significa elegibilidad según filtros; `persisted: false` confirma que esta
herramienta de auditoría no aplica parámetros a la cuenta. Ninguna combinación pasó.
Scalper/BTC obtuvo PnL positivo con solo cuatro operaciones y ninguna operación bajo
costes duplicados; se rechazó por muestra insuficiente y falta de robustez.

## Verificación y reproducción

```powershell
$env:PYTHONPATH='backend'
backend/.venv/Scripts/python.exe -m unittest discover -s backend/tests -v
backend/.venv/Scripts/python.exe backend/evaluate.py --bot swing --symbol BTC --output reports/btc-swing-validation.json
cd frontend
npm run build
```

17 pruebas de regresión pasan: gaps, cierre final, ejecución posterior a la señal,
ambigüedad intravela, cambios de dirección, causalidad en un fixture de los seis bots,
fechas, cotizaciones, persistencia, riesgo, revocación y cambio de día. El frontend compila.
Las pruebas usan bases temporales; no escriben en la cuenta del usuario.

## Límites y activación

Los cambios entran en vigor al reiniciar el backend. No se reinició un proceso existente
ni se abrieron operaciones como parte de esta auditoría. Reentrenar desde el panel
actualiza las validaciones con este motor; el planificador también lo hará en sus ciclos.
Se conservó el modo exploratorio previo: incluso mercados rechazados pueden operar con
riesgo reducido. No debe interpretarse como una estrategia validada.

El backtest sigue siendo una aproximación por velas: no replica completamente el
meta-modelo, los límites de cartera, cooldown escalonado, filtro de extensión ni la
secuencia de ticks. No modela comisiones específicas de bróker, financiación, swaps,
liquidaciones o profundidad de mercado. El stress duplica el spread estimado, no todos
los costes posibles. El bot ORB aún requiere un calendario completo de sesiones y cierres
anticipados para garantizar cierre intradía. El oro sigue usando velas proxy reescaladas.
Las operaciones antiguas sin riesgo inicial guardado conservan una aproximación; no se
puede reconstruir exactamente su stop inicial después de que se desplazara.

El siguiente paso para investigar rendimiento es ampliar la evaluación por mercado con
ventanas nuevas y reservadas, y comparar estrategias simples y costes del bróker real.
No se deben ajustar las reglas repetidamente contra estos mismos resultados de validación.
