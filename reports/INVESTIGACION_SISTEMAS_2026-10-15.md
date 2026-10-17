# Investigación e implementación de sistemas de negociación

Fecha: 15 de octubre de 2026, Europe/Madrid. Investigación basada en fuentes originales de autores, NBER y documentación de ejecución. Los resultados empíricos de una clase de activos o un horizonte no se trasladan automáticamente a criptomonedas de cinco minutos.

## Qué tiene mejor fundamento y qué se ha implementado

**No se ha identificado un ganador universal.** La evidencia más extensa entre las familias que encajan con los datos disponibles corresponde al seguimiento de tendencia diversificado con horizontes relativamente lentos. Reducir riesgo cuando aumenta la volatilidad tiene respaldo adicional; elevar el número de indicadores, operaciones o parámetros no constituye evidencia de ventaja. La reversión corta merece pruebas especialmente exigentes de costes.

| Familia o método | Evidencia original y límites | Implementación en este proyecto |
|---|---|---|
| Tendencia y momentum temporal | Moskowitz, Ooi y Pedersen estudian 58 contratos de futuros/forwards y señales basadas en retornos de meses, en una muestra de más de 25 años. No es un estudio de scalping cripto. | Comparador con Donchian causal y momentum de tres horizontes, tres vecindades por familia. Se estudian 5m, 15m y 1h como hipótesis nuevas. |
| Diversificación de tendencia | Hurst, Ooi y Pedersen extienden una simulación diversificada a 1880 y examinan distintos entornos. Requiere instrumentos, financiación y costes propios de esa cartera. | La evaluación conserva instrumentos, fuentes, intervalos y resultados separados; no inventa una cartera diversificada al sumar cuentas o retornos en monedas diferentes. |
| Riesgo gestionado por volatilidad | Moreira y Muir documentan ventajas al reducir riesgo en periodos de volatilidad alta para varios factores y carry de divisas. Ese resultado no valida cualquier fórmula intradía. | Control causal de reducción de riesgo entre 25% y 100%, comparado contra el mismo momentum sin esa reducción. Nunca aumenta apalancamiento. El dimensionamiento base por ATR y coste neto también reduce cantidad en volatilidad alta. |
| Reversión en rango | Frazzini, Israel y Moskowitz muestran que los costes pueden destruir reversión corta, incluso cuando otras primas sobreviven. Su universo es de acciones desarrolladas; no prueba que toda reversión cripto pierda. | Familia experimental de z-score con ADX bajo, salida al recuperar la media y examen a doble fricción. Mantiene un registro de tres ventanas vecinas. |
| Validación y selección | Bailey y colaboradores explican cómo probar numerosas variantes produce falsos ganadores y por qué un único holdout no elimina ese riesgo. | Rejilla reducida, número de ensayos visible, tres bloques temporales, embargo según permanencia, holdout final reservado, vecinos y estrés de costes. La salida etiqueta expresamente que su intervalo no es PBO ni DSR. |

Fuentes primarias:

1. [Moskowitz, Ooi y Pedersen, *Time Series Momentum*, 2012](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum).
2. [Hurst, Ooi y Pedersen, *A Century of Evidence on Trend-Following Investing*, 2017](https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing).
3. [Moreira y Muir, *Volatility Managed Portfolios*, NBER 22208, 2016; publicado en 2017](https://www.nber.org/papers/w22208).
4. [Frazzini, Israel y Moskowitz, *Trading Costs of Asset Pricing Anomalies*, 2012](https://www.aqr.com/insights/research/working-paper/trading-costs-of-asset-pricing-anomalies).
5. [Bailey, Borwein, López de Prado y Zhu, *The Probability of Backtest Overfitting*, 2015, copia de los autores](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
6. [Binance, documentación original de comisiones por cuenta/símbolo](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/commission_faq.md).

## Arquitectura aplicada

Se ha separado la investigación del libro de cuentas. Las señales puras reciben únicamente velas conocidas; la simulación ejecuta una señal de cierre en la apertura posterior y contempla gaps, prioridad conservadora del stop cuando una vela toca ambas barreras, cierre de posiciones al terminar cada tramo y pérdidas flotantes en drawdown. Las funciones económicas comunes calculan fills, comisiones, deslizamiento y financiación antes de expresar el PnL neto.

El entrenamiento de los bots existentes conserva sus escuelas de señal. La nueva versión 4 utiliza costes del mismo escenario que el Laboratorio, reserva coste de salida y financiación máxima al dimensionar y rechaza objetivos netos negativos. Las validaciones anteriores dejan de autorizar parámetros porque corresponden a otro motor económico. No se presenta el replay genérico como validación de un contrato MT5: su adaptador debe acreditar lotes, margen, divisa y costes del bróker.

`research_strategies.py` contiene doce variantes predefinidas: tres Donchian, tres momentum, sus tres controles de momentum gestionado por volatilidad y tres reversiones en rango. Estas familias están implementadas para investigación y no se conectan a órdenes reales o demo automáticamente. La decisión de promoción necesita una validación ejecutable y datos futuros; el proyecto puede seguir observando y operando sus políticas existentes con sus límites de riesgo.

## Protocolo de entrenamiento existente

1. Seleccionar la configuración de ejecución en el 45% inicial. La rejilla se ha reducido de centenares de combinaciones a un máximo de 72 antes de excluir geometrías de TP/SL incompatibles.
2. Congelar esa configuración y medir tres bloques posteriores hasta el 80% de la muestra. Cada tramo arranca sin posiciones y separa al menos 30 velas o la permanencia máxima configurada, lo que sea mayor.
3. Comprobar costes dobles, resultados positivos en los bloques y vecinos de umbral/tamaño de stop/objetivo sin elegir el vecino que más gane.
4. Estimar incertidumbre mediante bootstrap circular por bloques de cierres. El intervalo es descriptivo: **no corrige todas las comparaciones múltiples ni demuestra independencia**.
5. Consultar el holdout final del 20% solo después de congelar la configuración. Exigir cierres suficientes, PnL positivo, costes reforzados y controles anteriores para aplicar parámetros.

Si una nueva configuración se construye después de mirar un holdout, este deja de ser un examen final intacto. Los informes deben archivarse y el seguimiento posterior a la fecha del informe aporta la siguiente evidencia. No se afirma haber implementado el estimador académico completo CSCV/PBO ni una prueba estadística definitiva.

## Comparador de familias nuevas

La interfaz `await app.research.run_research(symbols, intervals, days, max_bars)` ofrece un informe JSON sin modificar bases de datos, saldos ni órdenes. Por defecto compara BTC, ETH y SOL en 5m, 15m y 1h durante hasta 30 días, con un máximo de 9.000 velas por conjunto. Admite hasta cinco símbolos, tres intervalos y 10.000 velas por conjunto; cada descarga tiene un máximo de diez páginas. La CLI `python -m app.research` permite guardar el informe con `--output`, rechazando sobrescribir un archivo anterior.

El comparador utiliza Spot largo, sin apalancamiento, una referencia fija de 100 USDT y riesgo de una unidad por operación. **No representa una cuenta de 100 EUR capitalizando** y no debe confundirse con las cuentas del panel. Cada instrumento se compara con efectivo sin intereses y con comprar y mantener, ambos explícitos. La referencia comprar y mantener invierte el nominal disponible completo y puede tener una exposición media diferente a las estrategias.

Las doce reglas fijas se comparan en tres bloques de descubrimiento anteriores al holdout. Se elige como máximo una; si ninguna supera efectivo y costes dobles con cierres suficientes, el holdout de estrategias se queda sin consultar y la selección es efectivo. Para el candidato congelado se calculan una sola evaluación final y su estrés de costes; los demás candidatos no se reordenan mirando ese tramo. Los vecinos se examinan únicamente en descubrimiento. La condición `candidateEligible` significa candidato para seguimiento, nunca una orden de promoción; `promotionApplied` siempre es falso.

Cada resultado conserva hash SHA-256 de OHLCV, fechas efectivas, número de velas, fuente/símbolo, versiones del motor y economía, parámetros, número de ensayos, métricas de cada bloque, costes, incertidumbre y razón de aceptación o rechazo. Si el límite de barras reduce el periodo disponible, las fechas efectivas permiten detectarlo. Los resultados incluyen familias fallidas y muestras insuficientes para evitar presentar solamente el mejor backtest.

La comisión, spread, deslizamiento y financiación de los perfiles comunes son **hipótesis explícitas de simulación**, no una tarifa de cuenta certificada. El comparador Spot no cobra financiación porque no toma prestado. No valida divisas, oro spot o contratos MT5 utilizando un proxy de Binance. El tamaño mínimo ejecutable, profundidad real del libro, impacto variable, préstamos y disponibilidad histórica de instrumentos siguen necesitando datos específicos antes de pasar a ejecución.

## Qué no conviene concluir todavía

Una semana o un mes no basta para declarar cuál es el mejor sistema económico ni para estimar rentabilidad anual. Tampoco hay evidencia local para sustituir las reglas por un LLM, aprendizaje por refuerzo o una selección masiva de indicadores. Antes de añadir ese grado de libertad se necesita una referencia simple neta estable, más historia, periodos de caída/rango/tendencia y seguimiento futuro. Las nuevas familias ofrecen ahora esa referencia reproducible.

El siguiente paso económico es comparar periodos extensos, conservar todos los informes y validar en observación los candidatos que sobrevivan. Si ninguno lo hace, mantener efectivo es un resultado válido del sistema, no un fallo que deba resolverse relajando filtros.

## Ejecución con datos reales y resultado inicial

Se ha ejecutado el comparador de solo lectura con BTC, ETH y SOL, intervalos de 5m/15m/1h, 30 días solicitados y límite de 9.000 velas. El informe final se generó el **15 de octubre de 2026 a las 22:07:24, Europe/Madrid**. La primera barra de 5m es del 15 de septiembre a las 22:10 y el final efectivo del conjunto es el 15 de octubre a las 22:05. Los marcos de 15m y 1h usan exclusivamente sus propias velas cerradas; cada fila del JSON conserva sus fechas exactas.

El [informe JSON completo](C:/Users/mario/Desktop/Trading/.runtime/research-latest-result.json) registra las 108 combinaciones predefinidas —doce reglas por nueve conjuntos—, sus bloques y sus escenarios de costes. Hay 8.639 velas por símbolo en 5m, 2.879 en 15m y 719 en 1h. Las fechas corresponden a una ventana reciente concreta, no a múltiples ciclos de mercado.

Hipótesis económica utilizada: comisión de **0,10% por lado**, deslizamiento adverso de **0,02% por lado**, spread completo de **0,05%** y financiación cero por ser Spot sin préstamo. La fricción de ida y vuelta ronda el 0,29% del nominal antes de variaciones de precio. En estrés se duplican comisión, deslizamiento y spread. Son escenarios sin verificar con una cuenta, no tarifas prometidas de Binance.

La siguiente tabla presenta **el rango de PnL neto de las doce variantes en los tres bloques de descubrimiento**, expresado en USDT por una referencia fija de 100 USDT. No son resultados del holdout ni rendimientos de las cuentas del panel. Los cierres varían según cada regla; mostrar el intervalo completo evita elegir únicamente el mejor resultado.

| Símbolo | Marco | PnL neto de las doce variantes | Cierres por variante | Candidato que supera el protocolo |
|---|---|---:|---:|---|
| BTC | 5m | −7,79 a −1,42 | 2 a 19 | Ninguno |
| BTC | 15m | −4,25 a +1,25 | 3 a 15 | Ninguno |
| BTC | 1h | −1,00 a +1,49 | 0 a 3 | Ninguno |
| ETH | 5m | −10,64 a −1,87 | 6 a 24 | Ninguno |
| ETH | 15m | −5,64 a +1,60 | 3 a 16 | Ninguno |
| ETH | 1h | −2,15 a +0,17 | 0 a 4 | Ninguno |
| SOL | 5m | −18,22 a −3,88 | 12 a 48 | Ninguno |
| SOL | 15m | −9,13 a −0,95 | 5 a 19 | Ninguno |
| SOL | 1h | −1,96 a +1,61 | 0 a 3 | Ninguno |

**Las 36 variantes de 5m pierden dinero neto en descubrimiento en esta muestra.** Algunos resultados de 15m y 1h son positivos, pero corresponden a muy pocos cierres y a un único bloque positivo; no proporcionan muestra ni consistencia suficiente. No se interpreta ese resultado como prueba de que toda estrategia de cinco minutos pierda ni de que una hora sea ganadora.

En los nueve conjuntos, `selected` es nulo, `candidateEligible` es falso y **no se ha consultado el holdout de ninguna estrategia**. El informe conserva las referencias finales de efectivo y comprar y mantener, pero esos precios no han intervenido en la selección. No se ha promocionado ninguna familia, cambiado saldos, abierto órdenes ni modificado bases de datos durante la investigación.

Este resultado confirma la prioridad económica: no activar una regla solo porque produzca más señales. La investigación necesita más historia, escenarios de costes calibrados y datos futuros. Con esta muestra no existe un sistema nuevo que pueda presentarse honestamente como superior y apto para escalar la inversión.

La ejecución final incluye la corrección del equilibrio neto del trailing para guardar paridad económica con el Laboratorio. Se ha conservado aparte la salida anterior a esa corrección, antes de inspeccionar sus resultados; no se han cambiado las reglas, umbrales o criterios para hacer pasar un candidato. La aproximación por barras no reproduce todos los caminos intrabar de un replay de ticks, y el bootstrap sobre cierres conserva esa limitación.

Validación técnica del comparador: catorce pruebas nuevas verifican causalidad por prefijos, ausencia de señales falsas en datos planos, conciliación de comisiones/deslizamiento, equilibrio neto del trailing largo/corto, bloqueo de objetivos netos negativos, control de ventas cortas, separación temporal, reserva del holdout, límites de descarga, rechazo de proxies y determinismo de incertidumbre. La suite completa disponible antes de añadir el caso final de equilibrio superó 215 pruebas; otros módulos pueden ampliar esa cifra en la verificación final.
