# Revisión de todas las estrategias y perfil agresivo — 21/10/2026

## Qué se ha cambiado

Motor v7, perfil `aggressive-adaptive-v3`, en simulación y MT5 DEMO. Se conservan saldos, operaciones y memoria del aprendizaje. Las posiciones existentes conservan sus stops, objetivos, trailing y duración guardados; las reglas nuevas se aplican a entradas nuevas.

| Componente | Comportamiento nuevo |
|---|---|
| Las tres mesas | Hasta 4 posiciones; máximo 25% del capital por entrada; riesgo objetivo 0,75% por entrada y 3% agregado en cada mesa. Antes: 3 posiciones, 16,67%, 0,5% y 1,5%. Los límites globales siguen aplicándose además de los locales. |
| Mesa Cripto | Rupturas de 10 velas y recuperaciones tras retroceso, con tendencia del marco superior cerrada y ADX. Stop 2 ATR, referencia 6 ATR, realización aproximada de un tercio a 1,5 R netos y dos tercios disponibles para continuación. Equilibrio neto y trailing a 2 R; máximo 48 h. |
| Mesa Forex | Señales de continuación con confirmación superior; objetivo completo de 6 ATR y trailing de 2,5 ATR después de 2 R. Mantiene cotizaciones frescas, costes, sesión y plazo de 6 horas. |
| Mesa Acciones | Igual estructura de continuación, pero exige beneficio/riesgo neto mínimo de 2,0 para evitar perseguir recorridos que apenas cubran costes. Cripto y Forex exigen 1,5. |
| Scalper | Pasa de 1m/5m a 5m/15m; ruptura corta o recuperación de medias, objetivo 4,8 ATR, stop 1,6 ATR, trailing 1,8 ATR y máximo 4 h. Se reduce la restricción spread/ATR a 25%, conservando costes completos en viabilidad y tamaño. |
| Swing | Pasa a confirmación de 4 h; ruptura o recuperación más temprana. Stop 2 ATR, objetivo 7 ATR y trailing 2,6 ATR. Espera 2 R para activar protección de ganancia y no estrecha antes de 4 R. |
| Squeeze | Liberación de compresión o expansión con ruptura y volumen. Stop 2 ATR, objetivo 8 ATR, trailing 2,8 ATR, máximo 72 h. La protección deja más recorrido al tramo ganador. |
| Apertura ORB | Rango inicial de 5 minutos; ruptura o retesteo, volumen relativo mínimo y dirección coherente. Stop 1,25 ATR y objetivo 5 ATR. |
| Tendencia, reversión y flujo | Se conservan entradas y parámetros económicos anteriores: las alternativas más abiertas empeoraron suficientes comparaciones como para descartarlas. En flujo se corrige además el cierre real del marco de 4 h en el entrenamiento. |
| Mercados sin validar | Tamaño a prueba del 25% del riesgo habitual y hasta 2 posiciones por bot. Antes: 15% y una. No se les otorga validación automática. |
| MT5 DEMO | Mantiene 2.500 EUR, riesgo base 0,5%, pérdida diaria máxima 2% y riesgo abierto máximo 2%. El riesgo a prueba pasa de 1,875 a 3,125 EUR por entrada. Los lotes mínimos siguen siendo obligatorios. |

No se amplían el apalancamiento máximo ni los límites globales: riesgo conjunto 3%, riesgo por factor 1,5%, exposición bruta 2 veces el patrimonio agregado y controles de concentración existentes. Cada cuenta conserva su moneda y su contabilidad.

## Fallos concretos corregidos durante la revisión

- **Valoración de exposición MT5:** solo se convertían correctamente instrumentos con EUR como base o divisa de beneficio. GBPUSD, USDJPY, oro y acciones en USD podían llegar al control global sin nominal en euros. Ahora se calcula con el contrato y un tick EURUSD fresco del mismo bróker. Sin conversión válida sigue bloqueado; no se inventa un tipo de cambio.
- **Causalidad de flujo:** el replay trataba un marco de 4 h como disponible tras una hora. Se exige su cierre completo, por lo que no se reutilizan resultados contaminados para promover validaciones.
- **Paridad de salidas:** las nuevas reglas de trailing están compartidas por ejecución, entrenamiento y replay MT5. Se guardan en cada entrada para no reinterpretarlas al reiniciar.
- **Diagnóstico de mesas:** el informe económico separa la política vigente de cada mesa, en lugar de atribuir a Forex/Acciones la versión de Cripto.

## Qué se comparó y qué salió

Se ejecutaron **36 comparaciones de bots** sobre BTC, ETH, SOL, EURUSD, GBPUSD, SPY y NVDA, según el universo de cada bot, y **7 comparaciones de mesas**. Se incluyen comisiones, spread, deslizamiento y, en el laboratorio, financiación. También se calcula estrés a costes 1,5x.

Las propuestas, iteraciones y resultados anteriores se conservan. Es una **exploración sobre datos conocidos**, no un holdout independiente ni una promesa de rentabilidad. En particular, se observaron las primeras variantes antes de descartar sus cambios perjudiciales. El tamaño de muestra del scalper es pequeño. Las distintas monedas o instrumentos no se suman como si fueran una única cuenta.

| Bot | Series comparadas | Series con mayor PnL neto en el perfil seleccionado |
|---|---:|---:|
| Scalper | 5 | 2 |
| Swing | 7 | 6 |
| Tendencia | 6 | 0; se conserva el perfil anterior |
| Reversión | 5 | 0; se conserva el perfil anterior |
| ORB | 2 | 2 |
| Squeeze | 5 | 5 |
| Flujo | 6 | 0; se conserva el perfil económico y se corrige causalidad |

Mejorar frente a una pérdida anterior **no equivale a ganar**. ORB pierde menos en las dos series, pero todavía pierde. Swing sigue siendo negativo en varios mercados. Algunos resultados positivos dejan de serlo bajo estrés. Por eso se mantiene tamaño a prueba y no se marca ninguna estrategia como validada a partir de esta comparación.

Ejemplos de las mesas, por cuenta independiente iniciada con 100 unidades de su moneda:

| Mesa / instrumento | PnL anterior | PnL agresivo | Agresivo con costes 1,5x |
|---|---:|---:|---:|
| Cripto BTC | −0,7791 USDT | +0,3762 USDT | −0,7888 USDT |
| Cripto ETH | −0,3450 USDT | −3,5246 USDT | −4,1227 USDT |
| Cripto SOL | +2,3789 USDT | +6,0722 USDT | +5,7487 USDT |
| Forex EURUSD | −0,2866 USD | −0,0713 USD | −0,1268 USD |
| Forex GBPUSD | −0,3848 USD | −0,2092 USD | −0,2387 USD |
| Acciones SPY | −0,0124 USD | −0,0335 USD | 0,0000 USD |
| Acciones NVDA | −0,4834 USD | +0,0308 USD | +0,0486 USD |

El estrés puede cambiar qué entradas superan el filtro de costes; un resultado menos malo bajo estrés no implica mejor ejecución. Estos replays son por instrumento y no reproducen la selección conjunta, la cola maker histórica ni toda la cartera. Se usa ejecución taker conservadora, sin inventar fills maker ni descuentos de comisión.

Resultados completos: [REVISION_AGRESIVA_2026-10-21.json](REVISION_AGRESIVA_2026-10-21.json). El periodo, configuración, métricas y versiones anteriores quedan registrados en `.runtime/aggressive-research-20261021/`.

## Comprobación y conservación

- 369 pruebas Python: causalidad de los siete perfiles, protección de posiciones existentes, caja, parciales, exposición MT5, costes, riesgo y persistencia.
- Compilación de la interfaz correcta; lint sin errores, con tres avisos preexistentes.
- Cuatro escenarios de arranque PowerShell. Reinicio del servicio y lectura de las políticas efectivas en las API locales.
- Copia SQLite anterior a los cambios: `.runtime/aggressive-backup/snapshot-20261021T154301930375Z/`.
- La interfaz muestra «Perfil agresivo en evaluación» y conserva el historial previo, separado de la nueva política.

Comprobación en funcionamiento a las 18:11 CEST: las tres mesas devuelven el perfil nuevo y los siete bots están activos. La mesa Cripto tiene cuatro posiciones, una abierta con la nueva política; Squeeze y Flujo han añadido una entrada cada uno. MT5 sigue conectado exclusivamente a DEMO, con 2.500 EUR virtuales y sin entradas nuevas: el escaneo muestra señales insuficientes, límites de exposición o cotizaciones antiguas. No se fuerzan órdenes para aparentar actividad.

Frente a la copia anterior se conservan los 82 identificadores del laboratorio y todos los de las tres mesas; el laboratorio registra ahora 84 operaciones y Cripto 44. Los siete modelos mantienen exactamente sus pesos y número de muestras; solo cambian la versión del motor y la fecha de actualización. No se han reiniciado balances ni añadido fondos en esta revisión. Evidencia de políticas y estado: `.runtime/aggressive-after.json`; captura: `.runtime/perfil-agresivo.png`.

La evidencia de seguimiento de tendencia a meses no demuestra que estos horizontes intradía ganen: [Moskowitz, Ooi y Pedersen — Time Series Momentum](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum). La hipótesis ORB con volumen relativo se contrasta localmente; no se importan rentabilidades de otro estudio: [Zarattini, Barbon y Aziz — A Profitable Day Trading Strategy for the U.S. Equity Market](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284).

El cambio permite operar de forma más agresiva y buscar mayores recorridos. Los resultados disponibles no permiten afirmar «mucho más beneficio» futuro en todos los mercados.
