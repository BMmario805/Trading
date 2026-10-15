# Auditoría del motor de trading — 9 de septiembre de 2026

## Actualización de correcciones — 15 de octubre de 2026

El diagnóstico original de abajo describe el estado del 9 de septiembre.
En el código actual se han verificado o completado estas correcciones:

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
