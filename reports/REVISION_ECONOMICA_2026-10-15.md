# Revisión del proyecto y de su rendimiento económico

Fecha: 15 de octubre de 2026. Zona horaria: Europe/Madrid.

El corte económico principal es **21:45:05**, con la sesión iniciada a las **20:56:41**: aproximadamente 48 minutos. Se revisaron código, API local, bases SQLite e información oficial externa. Las cifras son de simulación y demo, no beneficios de una cuenta real. Las lecturas de entrenamiento y diagnósticos se hicieron durante la revisión y pueden cambiar después del corte.

## Dictamen

El proyecto dispone de una base operativa útil para investigar estrategias: siete bots, una mesa Spot, conexión a una demo MT5, persistencia, controles de riesgo y pruebas automáticas. **Todavía no hay evidencia de una ventaja económica sostenible.** La prioridad es corregir los costes y las diferencias entre simulación y ejecución antes de aumentar frecuencia, exposición o capital.

El hallazgo más importante es verificable: Mesa Siete admite entradas cuyo objetivo, alcanzado exactamente, produce una pérdida después de costes. En los ajustes anteriores para incrementar frecuencia faltó este filtro económico. Las ganancias del Laboratorio tampoco incorporan todos los costes de una ejecución real.

## Qué funciona y cómo se organiza

| Componente | Funcionamiento actual | Capital y ejecución |
|---|---|---|
| Mesa Siete | Examina 505 pares Spot USDT; 63 eran aptos por liquidez en el corte. Busca hasta tres posiciones, velas de 5 minutos, ciclos nominales de 20 segundos, SL 1,2 ATR, TP 1,8 ATR, trailing desde 0,7R y permanencia máxima de 45 minutos. | Libro simulado financiado con 100 EUR convertidos a USDT. Cobra comisión fija de 0,1 % por lado y deslizamiento fijo de 0,02 % por lado. |
| Laboratorio | Scalper, Swing, Tendencia, Reversión, Apertura, Squeeze y Flujo. Comparten funciones de señal con el entrenamiento. Las entradas sin validación pueden explorar al 35 % del riesgo habitual. | **Siete cuentas independientes de 100 EUR: 700 EUR**, con conversiones de divisa fijadas al inicio. Simula spread y apalancamientos de 5 a 20 veces. |
| MT5 | Revalida cuenta DEMO autorizada, consulta cotizaciones, calcula riesgo con el bróker y redondea los lotes hacia abajo. Presupuesto compartido entre bots. | Seguimiento virtual de 100 EUR sobre una demo cuyo saldo del bróker se conserva. Los tamaños sí quedan limitados por el presupuesto virtual. |
| Gemini y sentimiento | Gemini clasifica textos y puede priorizar un lote de Mesa; existe alternativa determinista. El sentimiento aparece en el panel. | El sentimiento no interviene en la señal de los bots ni en el metamodelo actual. Los pesos visibles/configurados no equivalen a una contribución efectiva. |

En conjunto se están evaluando **900 EUR virtuales en cuentas separadas**, no una única cartera financiada con 100 EUR. Para evaluar el uso de 100 EUR reales haría falta una asignación común entre estrategias.

Hay controles valiosos: velas cerradas, cotizaciones con fecha, deduplicación de entradas, SL/TP, límites diarios, límites de riesgo, persistencia tras reinicios, embargo temporal en validación y rechazo de históricos proxy de oro como prueba del instrumento real. Las pruebas verifican reglas de funcionamiento; no prueban rentabilidad.

## Resultados desde el reinicio

| Entorno | Inicial | Patrimonio al corte | Operaciones cerradas | Lectura |
|---|---:|---:|---:|---|
| Mesa | 100 EUR | 99,58 EUR al cambio inicial | 6: una positiva y cinco negativas | −0,42 % de patrimonio; −0,4620 USDT realizados; tres posiciones abiertas. |
| Laboratorio, siete cuentas | 700 EUR | 701,41 EUR | 2: ambas negativas | +1,41 EUR incluyendo flotante; realizado −0,11 EUR. No incluye todos los costes ejecutables. Catorce abiertas. |
| MT5 virtual | 100 EUR | 100 EUR | 0 | Sin actividad cerrada con este presupuesto. No representa una estrategia ganadora ni perdedora. |

La muestra de ocho cierres entre todos los entornos, en menos de una hora, no permite clasificar estrategias ni extrapolar un retorno diario, mensual o anual. Tampoco es correcto comparar el beneficio bruto simulado del Laboratorio con el neto de Mesa como si las reglas de costes fueran iguales.

### Lo que está ocurriendo con las comisiones en Mesa

Reconciliación de los seis cierres, en USDT:

| Concepto | Importe |
|---|---:|
| Movimiento entre las referencias de compra ask y venta bid, antes del deslizamiento simulado y de comisiones | +0,07313 |
| Deslizamiento simulado de entrada y salida | −0,08919 |
| Comisiones de entrada y salida | −0,44594 |
| **Resultado neto realizado** | **−0,46199** |

El spread está implícito en los precios ask/bid de esa primera fila. Las comisiones representan aritméticamente el 96,5 % de la pérdida neta. No es una estimación causal de rentabilidad futura; es la reconciliación de estos cierres.

Las dos llamadas a Gemini registradas desde el inicio de la sesión sumaban aproximadamente **0,00735 USD estimados**. Su gasto mensual total de otras sesiones no debe cargarse entero a esta sesión. Hoy la comisión de negociación es un problema económico mayor que ese consumo de IA.

## Hallazgos y orden de corrección

### 1. Crítico para la economía: objetivo de beneficio insuficiente

[desk.py:114](C:/Users/mario/Desktop/Trading/backend/app/desk.py:114) dimensiona el riesgo y calcula `TP = entrada + 1,8 ATR`, pero no comprueba el beneficio neto del TP antes de admitir la orden.

| Operación persistida | Beneficio neto si sale exactamente en el TP inicial | Pérdida al SL inicial | Beneficio/riesgo neto |
|---|---:|---:|---:|
| RLUSDUSDT | −0,07779 USDT | 0,08396 USDT | Negativo |
| TRXUSDT | −0,04300 USDT | 0,10704 USDT | Negativo |
| BTCUSDT | +0,00536 USDT | 0,13949 USDT | 0,038 |

El TP bruto de BTC parecía positivo, pero bajo un resultado binario TP/SL fijo exigiría aproximadamente **96,3 % de aciertos** para equilibrarse. Esta cifra no describe el sistema completo porque existen trailing, deslizamientos y otras salidas; sirve para identificar una geometría económica desfavorable.

Con las hipótesis actuales, una ida y vuelta cuesta alrededor del 0,24 % del nominal en comisión y deslizamiento, además del diferencial del mercado. Una señal de volatilidad muy baja puede resultar inviable aunque sea técnicamente válida.

**Corrección propuesta:** calcular antes de entrar beneficio al TP, pérdida al SL y coste total netos. Rechazar siempre el TP neto no positivo. Después, evaluar la expectativa y el beneficio/riesgo por estrategia; un ratio superior a uno, por sí solo, tampoco demuestra ventaja. Una familia de reversión puede requerir alta tasa de acierto y una tendencia puede requerir otra distribución.

RLUSD no figura en la lista estática de monedas estables excluidas. Su emisor lo identifica como stablecoin vinculada al USD. Un catálogo de activos debe actualizarse y complementarse con viabilidad económica, para evitar depender de una lista manual incompleta. [Documentación de Ripple](https://docs.ripple.com/products/payments-direct-2/api-docs/integration-resources/digital-assets/rlusd).

La comisión fija del código es una hipótesis de simulación, no una tarifa verificada para una futura cuenta. Binance permite consultar las comisiones de cuenta/símbolo y contempla tipos y descuentos distintos. [Documentación oficial de comisiones de Binance](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/commission_faq.md).

### 2. Alto: el Laboratorio y MT5 no validan con el mismo modelo de ejecución

En [bots.py:39](C:/Users/mario/Desktop/Trading/backend/app/bots.py:39) y [bots.py:1034](C:/Users/mario/Desktop/Trading/backend/app/bots.py:1034), el Laboratorio utiliza spread fijo y PnL por diferencia de precio convertida a EUR. Faltan comisión, deslizamiento, financiación, préstamo de cortos, swaps, granularidad y liquidación. Permite posiciones cortas de cripto con cotizaciones de Binance Spot: el precio observado no acredita que esa modalidad sea ejecutable en Spot.

El entrenamiento MT5 llama al simulador genérico [mt5_demo.py:223](C:/Users/mario/Desktop/Trading/backend/app/mt5_demo.py:223). Este utiliza cantidades fraccionarias y un límite de nominal genérico; no reproduce contrato, lote mínimo, paso de lote, cálculo de beneficio por el bróker ni costes completos. También hay diferencias entre salidas por señal contraria y trailing del backtest y de MT5.

**Corrección propuesta:** un motor de ejecución por modalidad, usado tanto para evaluar como para ejecutar/simular: Spot largo con efectivo; margen o derivados con sus reglas; MT5 con las especificaciones de contrato del bróker. El replay debe reproducir señales, órdenes, salidas, capital, margen y costes de la misma forma.

### 3. Alto: presupuesto y exposición globales

Los límites de [bots.py:970](C:/Users/mario/Desktop/Trading/backend/app/bots.py:970) actúan por bot. No existe un límite común entre Mesa, siete cuentas del Laboratorio y MT5, ni uno por factor económico.

En una lectura de las posiciones, XAUUSD y PAXG largos sumaban aproximadamente **669 EUR de nominal** entre cuentas; son dos instrumentos con exposición al oro. También había ETH largo y corto en estrategias distintas. Squeeze simulaba unos 506 EUR de nominal sobre 100 EUR, y Swing unos 407 EUR sobre aproximadamente 100 EUR. El nominal no es dinero aportado ni pérdida máxima, pero sí revela exposición y costes potenciales.

**Mejora propuesta:** asignador global con capital disponible, riesgo agregado, exposición neta y bruta, concentración por factor y duplicación de señales. Mantener cuentas separadas para comparar estrategias es útil; debe distinguirse de ejecutar todas contra un mismo capital.

No propongo elevar el apalancamiento para compensar objetivos pequeños: amplifica pérdidas y costes sin corregir la expectativa por operación.

### 4. Alto: sesgos y fallos de aprendizaje

En [learning.py:326](C:/Users/mario/Desktop/Trading/backend/app/learning.py:326), el metamodelo aprende cierres con `id > lastTradeId`. El ID corresponde a la apertura. Si la operación 102 cierra antes que la 101, aprender la 102 hace que la 101 quede omitida cuando cierre después. Hay que registrar eventos de cierre o IDs procesados, con persistencia e idempotencia.

En [learning.py:427](C:/Users/mario/Desktop/Trading/backend/app/learning.py:427), `(winRate or 100)` trata un 0 % real como si fuera 100 %. Esto evita un veto direccional que debería activarse hasta que intervenga otro criterio. Debe distinguirse cero de valor ausente.

Los `createdAt` de los vetos se recrean en cada actualización ([learning.py:409](C:/Users/mario/Desktop/Trading/backend/app/learning.py:409)), mientras que la revalidación exige un entrenamiento posterior al veto. Debe conservarse el instante original del episodio y permitir comprobar si la revalidación corresponde a una configuración posterior.

El tamaño denominado Kelly es una transformación lineal de la probabilidad de ganar, sin relación beneficio/pérdida esperada ni calibración temporal suficiente. Una probabilidad de ganar no basta para estimar beneficio monetario. Primero hay que reparar las muestras y medir calibración y expectativa neta.

### 5. Alto: el riesgo comparte esperas de red con la búsqueda

[desk.py:467](C:/Users/mario/Desktop/Trading/backend/app/desk.py:467) gestiona stops antes de entrar en un escaneo secuencial. Cada petición puede consumir 15 segundos por host y hay dos hosts. Diez candidatos podrían retrasar varios minutos la próxima gestión si las fuentes fallan lentamente, además de las peticiones de las posiciones abiertas. Es un riesgo de diseño; no se observó ese retraso extremo en la revisión.

**Mejora propuesta:** cotizaciones y vigilancia de riesgo en una tarea independiente, con acceso corto y coherente al libro; tiempos máximos por candidato; concurrencia limitada para datos y una fase final serializada para comprometer órdenes. Publicar edad de datos, duración del ciclo, retraso del stop y estado de fuentes.

En MT5, un error de sincronización de resultados se registra, pero no bloquea automáticamente nuevas entradas que usen el saldo virtual desactualizado. Debe exigirse un libro reconciliado y fresco, especialmente después de cerrar posiciones. Las órdenes con respuesta incierta necesitan reconciliación por ticket antes de reintentar o liberar un bloqueo.

También hay un caso sin cubrir en la frescura de MT5: [mt5_demo.py:95](C:/Users/mario/Desktop/Trading/backend/app/mt5_demo.py:95) estima el desfase con el tick más reciente y [mt5_gateway.py:47](C:/Users/mario/Desktop/Trading/backend/app/mt5_gateway.py:47) reutiliza ese desfase para calcular su edad. Si todos los ticks llevan diez minutos congelados, el cálculo puede absorber esos diez minutos y hacer que el más reciente parezca de edad cero. Hay que separar un offset estable del servidor de la comprobación de que los ticks siguen avanzando, y probar el escenario de todos los mercados cerrados/congelados. Es un fallo reproducible del cálculo, no una constatación de que hoy se haya ejecutado una orden con ese problema.

### 6. Medio: validación y selección todavía insuficientes

Hay una separación 70/30, embargo y estrés de spread: son defensas útiles. Sin embargo, se prueban **640 configuraciones por par en la mayoría de bots y 800 en Reversión**, seleccionando sobre un único corte. La aceptación admite ocho operaciones de validación y dos por subperiodo. Reentrenar ventanas solapadas y retocar parámetros tras ver resultados reutiliza información y puede seleccionar ruido. [training.py:382](C:/Users/mario/Desktop/Trading/backend/app/training.py:382).

En la lectura había tres configuraciones aplicadas de 64 resultados guardados:

| Configuración | Cierres de validación | Factor de beneficio de validación | Factor de beneficio con spread duplicado |
|---|---:|---:|---:|
| Reversión / AAPL | 15 | 1,51 | 1,47 |
| Flujo / AVAX | 51 | 1,62 | 1,28 |
| Swing / ADA | 114 | 1,49 | 1,31 |

Son candidatos para investigar, **no recomendaciones de compra ni ganadores confirmados**. Trece de catorce posiciones abiertas del Laboratorio carecían de validación vigente y operaban en exploración. El estrés actual puede cambiar las propias entradas al modificar filtros; conviene añadir una prueba de costes sobre las mismas operaciones.

**Mejora propuesta:** evaluación con varios avances temporales, último tramo intocable, registro del número de búsquedas, estabilidad entre parámetros vecinos, intervalos de incertidumbre mediante remuestreo temporal y costes realistas. Después, una fase de seguimiento sin cambios de configuración. El trabajo original sobre sobreajuste en backtests explica por qué elegir entre muchas alternativas exige controles adicionales. [Bailey y colaboradores](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf), [Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).

### 7. Medio: viabilidad de MT5 con 100 EUR

MT5 estaba conectado, activo y sin operaciones desde el reinicio. De 45 diagnósticos: 38 sin cruce reciente, cuatro con lote mínimo superior al presupuesto, dos con cotización antigua y uno con precio alejado de la señal.

El riesgo base es 0,1 %: **0,10 EUR** sobre 100 EUR. En prueba se aplica el 35 %: **0,035 EUR**. La reserva del 10 % implica que la pérdida teórica al stop del lote mínimo debe caber aproximadamente en **0,0318 EUR** en prueba o **0,0909 EUR** validado. Además hay un límite de margen por entrada. No se consultaron nuevas especificaciones ni se enviaron órdenes durante esta revisión.

La condición de viabilidad por riesgo puede expresarse como:

`capital mínimo = 1,1 × pérdida del lote mínimo al stop / fracción de riesgo efectiva`.

Por tanto, el bloqueo por lote mínimo puede ser correcto. Los mínimos y pasos son propiedades del símbolo definidas por el bróker, no un filtro que convenga ignorar. [Referencia oficial de MQL5](https://www.mql5.com/en/docs/constants/environment_state/marketinfoconstants).

**Mejora propuesta:** mostrar lote mínimo, pérdida monetaria, margen y capital requerido por candidato. Comparar instrumentos/cuentas de menor granularidad en la evaluación; no elevar riesgo automáticamente para forzar entradas.

### 8. Medio: configuración y registros reproducibles

Hay un cambio local pendiente del usuario en [desk.py:120](C:/Users/mario/Desktop/Trading/backend/app/desk.py:120): `equity/6`. El proceso activo arrancó antes de ese cambio, sin recarga automática, y las entradas actuales siguen siendo aproximadamente un tercio del capital; también los textos anuncian un tercio. El cambio se preservó y no se reinició el servicio durante la revisión. Reducir el tamaño reduce importes, pero no arregla un TP neto negativo ni la relación beneficio/riesgo.

Es necesario publicar una versión de código/configuración activa y guardar esa versión, costes y especificaciones con cada operación. Agrupar resultados por sesión, configuración, modalidad y fuentes; evitar mezclar históricos después de retocar umbrales o reiniciar.

El cursor de Mesa incluye posiciones ya gestionadas en el avance de candidatos nuevos ([desk.py:316](C:/Users/mario/Desktop/Trading/backend/app/desk.py:316)). Puede saltar mercados de forma repetida. Deben separarse ambos contadores y probar cobertura determinista.

MT5 conserva timestamps de deals del servidor y los representa directamente junto a snapshots UTC ([mt5_performance.py:114](C:/Users/mario/Desktop/Trading/backend/app/mt5_performance.py:114)). La copia anterior muestra un desfase cercano a tres horas. Normalizar a UTC al presentar y filtrar, conservando la hora original para auditoría; el corte actual del reinicio utiliza la escala del servidor de forma explícita.

La referencia oficial indica UTC para los datos de ticks recibidos: cualquier peculiaridad observada del servidor debe identificarse y comprobarse expresamente, sin asumir que todas las fuentes comparten una misma desviación. [Documentación de tiempos de MT5](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksfrom_py).

La documentación también necesita una actualización: README y el informe histórico describen varios estados anteriores. Las versiones previas sirven como registro, no como especificación de lo que ejecuta el proceso actual.

## Plan de mejora propuesto

| Orden | Trabajo | Criterio de aceptación |
|---|---|---|
| 1 | Filtro económico neto, clasificación de stablecoins y conciliación de costes | Ninguna nueva entrada con TP neto no positivo; comisiones, spread y slippage reconciliados por ticket. Casos RLUSD/TRX/BTC como regresión. |
| 2 | Modelo de ejecución común por modalidad | Misma secuencia de señales/órdenes/salidas al reproducir datos; lotes, contrato, costes y moneda coherentes con la modalidad real. |
| 3 | Corrección del aprendizaje y de los tiempos | Aprender una sola vez todos los cierres, incluso fuera de orden; 0 % tratado correctamente; veto revalidable; eventos MT5 normalizados. |
| 4 | Asignador y vigilancia independientes | Riesgo global y por factor visible; stops no retrasados por un escaneo o una llamada a IA; datos y reconciliación frescos como requisito. |
| 5 | Experimento temporal con configuración congelada | Comparar 5m, 15m y 1h con el mismo capital y costes, sin elegir parámetros después de mirar el tramo final. Medir expectativa neta, drawdown, rotación y estabilidad por régimen. |
| 6 | Promoción gradual de estrategias | Asignar más presupuesto solo si la evidencia fuera de muestra y en seguimiento es compatible con expectativa neta positiva y pérdida tolerable; reducir si se deteriora. |

Los umbrales de beneficio/riesgo, el número de activos y la permanencia son hipótesis a contrastar. No hay fundamento todavía para afirmar que tres posiciones, 5m, 45 minutos o umbral 0,15 sean los parámetros económicamente óptimos.

La función objetivo debe ser **beneficio neto con un drawdown controlado**, descontando costes operativos y de negociación y mostrando incertidumbre. El número de operaciones, el número de mercados, el porcentaje de acierto o una mejora visual del panel no son sustitutos de esa medición.

## Comprobaciones y alcance

- 177 pruebas Python superadas.
- Compilación del frontend correcta; lint sin errores y con tres avisos previos en Watchlist/BotPerformance.
- Lecturas de API y SQLite para verificar capital, cierres, costes, diagnósticos y validaciones.
- Revisión sin modificar estrategias, parámetros, saldos, órdenes ni procesos activos. Se preservó el cambio local `equity/6`.
- Se guardaron instantáneas económicas reproducibles en `.runtime/project-review-snapshot.json` y `.runtime/project-review-metrics.json`; contienen información financiera local y no se publicaron.

Los hallazgos matemáticos y de implementación están sustentados por código y datos. Las propuestas de mejora de rentabilidad siguen siendo hipótesis hasta pasar la validación descrita.
