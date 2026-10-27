# Mesa Siete: investigación de estrategias y revisión económica

Fecha: 27 de octubre de 2026. Captura contable y de servicio: **20:27:38 CET, Europe/Madrid**.

## Dictamen

**La prioridad que considero mejor para este proyecto es investigar una base sencilla de tendencia y fuerza relativa, con poca rotación, dimensionamiento por volatilidad y control de concentración. La IA debe demostrar una mejora incremental frente a esa base antes de intervenir en su riesgo.** Es una prioridad de investigación adaptada a los datos y la infraestructura disponibles; no una estrategia ganadora ya acreditada ni una promesa de beneficios.

La revisión de código, libros, servicio y estudios anteriores no identifica una política de Mesa Siete con ventaja neta demostrada fuera de muestra. La documentación académica aporta hipótesis razonables, pero sus mercados, horizontes y condiciones de ejecución no son automáticamente los de este proyecto.

Se consultaron **20 fuentes primarias**: artículos y documentos de sus autores, repositorios universitarios, NBER, BIS y documentación del exchange. Se distinguen resultados publicados, documentos de trabajo y recomendaciones propias. No se usaron rankings comerciales de bots como evidencia de rentabilidad.

Esta entrega incluye un informe y evidencia. **No cambia motores, presupuestos, posiciones, cuentas, despliegue ni configuración.** Se han preservado los cambios de código que ya existían. Los archivos protegidos no se han modificado. El servicio observado continúa en paper y el enjambre en `shadow`, con `armed=false`.

## 1. Evidencia específica del proyecto

La [captura de evidencia](C:/Users/mario/Desktop/Trading/reports/INVESTIGACION_IA_EVIDENCIA_2026-10-27.json) procede de transacciones SQLite de solo lectura y consultas GET al servicio local. Cada libro tiene su propia instantánea; no existe una transacción simultánea entre todos ellos. Los PnL siguientes requieren versión explícita `net-costs-v1`: son netos **según el modelo registrado**, no comisiones ni fills reales certificados por un bróker. Se excluyen registros de costes desconocidos.

| Libro o cohorte | Cierres con costes explícitos | PnL cerrado del modelo | Interpretación |
|---|---:|---:|---|
| Cripto, historial de varias políticas | 41 | −21,6251 USDT | Historial descriptivo; no corresponde íntegramente a v9/v10. Otros 17 cierres quedan excluidos por costes no declarados. |
| Cripto v8, aislada | 4 | −2,3648 USDT | Muestra mínima; no permite juzgar otra versión. |
| Cripto v9 en el servicio | 0 | 0 USDT | Dos posiciones abiertas; todavía sin resultado cerrado de esa política. |
| Forex, varias políticas anteriores | 13 | −0,06556 USD | Muestra pequeña y PnL negativo. |
| Acciones, varias políticas anteriores | 6 | −0,22810 USD | Muestra pequeña y PnL negativo. |

No se suman divisas diferentes ni cuentas virtuales independientes. El efectivo persistido no se presenta como equity actual: hay posiciones abiertas y sería necesario valorarlas con cotizaciones vigentes.

Otros datos comprobados:

- Las 132 validaciones guardadas del laboratorio son motor 8; **ninguna figura aplicada**.
- Los siete metamodelos tienen entre **2 y 24 cierres aprendidos**; todos aparecen sin calibrar. Los modelos persistidos no incluyen aún una versión de regularización L1.
- El registro de decisiones en uso tiene **7 candidatos, 12 decisiones y cero resultados**. Solo aparecen brazos `rules_local` y `rules_plus_ai`.
- A5–A7 mantienen contratos de investigación, pero no hay eventos de parejas de resultados en el registro examinado.
- El código contiene `desk-rs-chandelier-1h-v10`; la API del proceso en marcha informa **v9**. La implementación reciente y el servicio activo son estados diferentes.

La suite completa se volvió a ejecutar: **638 pruebas aprobadas, 158 subpruebas aprobadas, 5 omitidas y ninguna fallida**, en 61,81 s. Las omisiones corresponden a permisos de enlaces simbólicos en Windows. Hay una advertencia de dependencia Starlette/httpx. Esto verifica contratos de software; no rentabilidad ni ausencia de todos los defectos económicos.

El frontend también supera compilación de producción y lint. Conserva tres avisos de React y un aviso por un paquete JavaScript superior a 500 kB; no hubo errores de compilación. No se ha reiniciado el servicio ni sustituido la versión que atiende las consultas.

### Estudios históricos disponibles

| Estudio conservado | Alcance | Resultado |
|---|---|---|
| 24/10, familias simples | 8 datasets, 96 combinaciones | Cero elegibles. LINK/4h llegó al holdout: 7 cierres, +1,81 USDT por referencia de 100; límite inferior de expectativa −0,2682 USDT. |
| 18/10, horizontes | 6 datasets, 72 combinaciones | Cero elegibles. |
| 18/10, tres años en 4h | 3 datasets, 36 combinaciones | Cero elegibles. Holdouts: BTC −3,58, ETH −4,12 y SOL +3,00 USDT, cada serie con su propia referencia de 100. SOL tuvo solo 8 cierres y límite inferior de expectativa negativo. |
| A1–A7 del 27/10 | Hipótesis fijas y capturas anteriores a su congelación | A1/A2 insuficientes o rechazados; A3/A4 bloqueados en esa ejecución histórica; A5–A7 pendientes. |

Son ejecuciones antiguas, con versiones y ventanas distintas, algunas solapadas. No se suman para fingir una única muestra independiente. Los informes anteriores no incorporan automáticamente los cambios recientes de cohortes ni validan v10.

Referencias locales: [estudio del 24/10](C:/Users/mario/Desktop/Trading/reports/grok-only-research-20261024.json), [horizontes](C:/Users/mario/Desktop/Trading/reports/profit-horizons-2026-10-18.json), [tres años](C:/Users/mario/Desktop/Trading/reports/profit-swing-3y-2026-10-18.json), [A1–A7](C:/Users/mario/Desktop/Trading/reports/professional-research-2026-10-27.json).

## 2. Qué estrategias merecen prioridad

La clasificación siguiente es mi juicio sobre **encaje y coste de validación en Mesa Siete**, no un ranking universal de rentabilidades futuras.

| Enfoque | Prioridad | Aplicación propuesta | Principal condición |
|---|---|---|---|
| Tendencia / momentum temporal | Alta | Regla simple en diario; 4h como hipótesis separada | Ventaja neta y estabilidad, sin extrapolar estudios mensuales a 1h. |
| Fuerza relativa / momentum transversal | Alta | Universo líquido conocido en cada fecha, filtro de tendencia absoluta y baja rotación | Evitar supervivencia, concentración y cambios continuos de líder. |
| Gestión de volatilidad y concentración | Alta | Normalizar riesgo entre señales y activos, sin elevar exposición para compensar pérdidas | Validar a escala de cartera y con límites existentes. |
| ML supervisado como filtro | Segunda fase | Logística regularizada y árboles poco profundos frente a reglas | Muestra suficiente, validación temporal y utilidad monetaria incremental. |
| Noticias procesadas por LLM | Segunda fase | Eventos verificables, detección de duplicados y prueba de veto | Disponibilidad real, latencia y modelo congelado prospectivamente. |
| Maker como método de ejecución | Segunda fase | Optimizar la ejecución de una señal ya válida | Medir fills, no-fills, selección adversa y oportunidad perdida. |
| Reversión a la media | Secundaria | Familia independiente, solo donde sobreviva a costes | Riesgo de tendencia persistente y alta sensibilidad a fricciones. |
| Pairs trading / arbitraje estadístico | Posterior | Relaciones económicas estables y ejecución de dos patas | Préstamo, cortos, ruptura de relación, financiación y costes. |
| Carry spot/futuros | Proyecto separado | Investigación neutral en delta, sin habilitarla en la mesa spot | Margen, liquidación, base, contraparte y disponibilidad contractual. |
| Scalping / market making como fuente principal | Baja ahora | Conservar como investigación de microestructura | Datos y ejecución aún insuficientes para acreditar ventaja a esa escala. |
| RL o consenso de más agentes | Baja ahora | Solo con un simulador y comparadores mucho más fuertes | Complejidad adicional sin suficiente evidencia propia. |

### Tendencia y momentum: la primera hipótesis

Hurst, Ooi y Pedersen documentan persistencia histórica de seguimiento de tendencias en múltiples clases de activos. Su estudio no acredita parámetros concretos de una mesa cripto horaria. Lo relevante es investigar una familia económica simple y contrastable, no copiar su rentabilidad. [Estudio original de AQR](https://www.aqr.com/insights/research/journal-article/a-century-of-evidence-on-trend-following-investing).

Liu y Tsyvinski encuentran momentum temporal y relaciones con atención en criptomonedas; Liu, Tsyvinski y Wu estudian factores de mercado, tamaño y momentum en su sección transversal. Son razones para probar estas familias, con universos y costes actuales, no para suponer que un ranking 24h/72h ya funciona. [Momentum cripto](https://www.nber.org/papers/w24877), [factores cripto](https://www.nber.org/papers/w25882).

Para Mesa Siete empezaría con dos hipótesis congeladas: una regla diaria de tendencia y otra de fuerza relativa con exposición larga o efectivo. BTC/ETH pueden servir como primer conjunto acotado por disponibilidad de historia, no como recomendación actual de compra. Ampliaría después el universo con criterios de liquidez y antigüedad fijados antes de observar rendimientos. El ranking debe usar únicamente activos que realmente existían y podían negociarse en cada fecha, incluidos los que posteriormente desaparecieron.

Separaría 1d, 4h y 1h como experimentos diferentes. Añadiría un margen entre la condición de entrada y la de salida del ranking para estudiar si reduce rotación. No buscaría decenas de umbrales hasta encontrar una curva atractiva.

### Volatilidad y cartera

Moreira y Muir encuentran mejoras históricas al reducir riesgo cuando aumenta la volatilidad en las estrategias que estudian. Eso no convierte en óptimo cualquier escalado por ATR ni garantiza el mismo efecto en cripto. [Artículo de referencia](https://www.nber.org/papers/w22208).

La prueba útil aquí es comparar, con igual presupuesto máximo, riesgo fijo frente a riesgo ajustado por volatilidad y concentración. Se debe medir la cartera entera: seis altcoins largas pueden representar una sola exposición al mercado cripto. Los límites globales de riesgo existentes siguen siendo controles independientes; no propongo alterarlos para generar más operaciones.

### ML supervisado y LLM: funciones distintas

Gu, Kelly y Xiu encuentran ganancias predictivas en un amplio panel histórico de acciones; interacciones entre momentum, liquidez y volatilidad resultan relevantes. La muestra y la frecuencia de ese trabajo son muy diferentes de unas decenas de cierres de un bot. [Investigación original](https://www.nber.org/papers/w25398).

Mi secuencia sería: reglas deterministas; logística regularizada; un modelo pequeño de árboles; y solo después modelos más complejos. Cada modelo debe superar al anterior después de costes. El metamodelo actual optimiza una puntuación logística ponderada por R: **esa puntuación no es una probabilidad de ganar calibrada ni una expectativa monetaria**. Evaluaría calibración temporal, pérdidas probabilísticas si el objetivo es probabilidad y, sobre todo, mejora neta de la política completa.

Los LLM tienen mejor encaje inicial en clasificar eventos, estructurar noticias y documentar decisiones. López-Lira y Tang distinguen una reacción inicial no negociable con alto acierto y una deriva posterior potencialmente negociable; también observan deterioro conforme aumenta la adopción. Un titular de “90 % de acierto” no implica que el bot pueda capturar ese movimiento. [Versión de los autores, 2025](https://arxiv.org/abs/2304.07619).

Fechar un artículo no basta para validar un backtest con un LLM entrenado después de los hechos. Glasserman y Lin estudian contaminación y distracción; otro trabajo en Economics Letters examina memoria predictiva. La anonimización es una prueba de sensibilidad, no una garantía de eliminar toda fuga. La evaluación más defendible para los modelos actuales es guardar **respuestas realmente emitidas antes de los resultados futuros**. [Glasserman y Lin](https://arxiv.org/abs/2309.17322), [AI’s predictable memory in financial analysis](https://www.sciencedirect.com/science/article/pii/S0165176525004392).

### Otras familias: por qué no las pondría primero

La reversión puede remunerar provisión de liquidez y asumir riesgos específicos de estrés; no es simplemente comprar cualquier caída. Nagel estudia esa relación en acciones. Los costes de ejecución de corto plazo pesan especialmente en la reversión. [Nagel](https://www.nber.org/papers/w17653), [Frazzini, Israel y Moskowitz](https://pages.stern.nyu.edu/~afrazzin/pdf/Trading%20Cost%20of%20Asset%20Pricing%20Anomalies%20-%20Frazzini%2C%20Israel%20and%20Moskowitz.pdf).

Pairs trading cuenta con evidencia histórica en acciones, pero una correlación elevada entre dos tokens no demuestra que su diferencia vaya a revertir. Las dos patas, sus riesgos y costes deben existir en el contrato de ejecución. [Gatev, Goetzmann y Rouwenhorst](https://www.nber.org/papers/w7032).

El carry spot/futuros tiene una fuente de retorno diferente. BIS documenta su tamaño y sus fricciones, incluidos margen y liquidación. No es una rentabilidad segura ni puede implementarse fielmente solo con un motor spot. [Crypto carry](https://www.bis.org/publications/working-paper-1087-crypto-carry).

El market making exige controlar inventario y el flujo que ejecuta contra nuestras órdenes. El modelo de Avellaneda y Stoikov sirve como fundamento conceptual; sus resultados de modelo no prueban que una cola simulada con mejores bid/ask reproduzca nuestra prioridad real. [Trabajo original](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf).

La baja prioridad de RL y de añadir agentes es una decisión de ingeniería para la situación actual: faltan comparadores económicos y muestras suficientes. No es una afirmación de que esos métodos nunca puedan funcionar.

## 3. Hallazgos concretos que condicionan los beneficios

Las siguientes observaciones incluyen límites de las mejoras recientes. Tener nuevas funciones y pruebas no acredita que se haya cerrado todo el problema de medición.

### P1 — Código v10 y proceso v9 no coinciden

El [código de política](C:/Users/mario/Desktop/Trading/backend/app/desk.py:69) declara v10; la captura GET informa v9. No se debe atribuir a v10 ninguna operación de ese proceso. Tampoco se debe cambiar retroactivamente la política de posiciones existentes.

**Aceptación propuesta:** registrar identificador de compilación, hash, política y fecha de arranque; verificar cuatro recibos por candidato nuevo y un cierre conciliado en una cuenta aislada. Un reinicio o despliegue sería una acción operativa posterior a esta revisión, no una tarea realizada aquí.

### P1 — Se pierde el vínculo del candidato al solicitar la auditoría

En [desk.py:1271](C:/Users/mario/Desktop/Trading/backend/app/desk.py:1271) se obtiene contexto que recibe `candidateId` durante la captura; en [desk.py:1300](C:/Users/mario/Desktop/Trading/backend/app/desk.py:1300) se reconstruye `economics`, y [la petición posterior](C:/Users/mario/Desktop/Trading/backend/app/desk.py:1313) usa ese segundo objeto sin el identificador.

Reproducción en una base temporal con los dobles de las pruebas: candidato registrado, petición de auditoría realizada y `entryAuditHasCandidateId=false`. Se abrió exclusivamente una posición simulada del libro temporal. Esto puede romper la correspondencia candidato/quorum; no demuestra una orden real indebida. Además, una respuesta posterior de IA no debe sobrescribir las decisiones congeladas inicialmente.

**Aceptación propuesta:** prueba integral candidato → petición → respuesta → autorización, con identidad conservada, fases separadas y rechazo de respuestas tardías que pretendan reescribir el pasado.

### P1 — A6/A7 aún no identifican el valor económico de los vetos

El [cierre atribuido](C:/Users/mario/Desktop/Trading/backend/app/desk.py:517) escala el PnL observado para brazos que aprobaron. Si reglas e IA aprueban con tamaño igual, sus resultados son iguales por construcción. Si la IA veta, ese brazo no recibe un resultado de operación. El [colector](C:/Users/mario/Desktop/Trading/backend/app/research_plans.py:90) incorpora una pareja solo si dispone de ambos resultados.

Por tanto, estas parejas ayudan a estudiar exposición y costes bajo una trayectoria compartida, pero **no estiman por sí solas el valor de las operaciones evitadas**. La muestra común de aprobaciones deja fuera precisamente decisiones en las que el filtro cambia la entrada. Noticias disponibles y mejora causada por noticias son afirmaciones distintas.

**Aceptación propuesta:** cuatro libros shadow prospectivos con el mismo capital, presupuestos y oportunidades. Seguir por separado entradas, permanencia en efectivo, salidas y restricciones de cartera. Un veto significa ausencia de posición en ese libro; no se inventa un fill ni un PnL de una operación inexistente. Comparar tanto las oportunidades discordantes como los flujos completos de las políticas, etiquetando las simulaciones.

### P1 — A5 condiciona la comparación a los fills maker

La [alternativa taker](C:/Users/mario/Desktop/Trading/backend/app/desk_maker.py:106) se congela al publicar la maker; su resultado se reconstruye cuando se cierra una posición maker y comparte su calendario de salidas. Los casos sin posición maker y las trayectorias de salida independientes requieren tratamiento adicional. El modelo de cola declara correctamente que no certifica ejecución real.

**Aceptación propuesta:** registrar todas las oportunidades, incluidos no-fills, cancelaciones y fills parciales; seguir el taker desde su propia entrada; medir precio frente a la cotización posterior, coste de espera y oportunidad perdida. La media de las operaciones maker rellenadas no es una comparación suficiente contra todos los taker.

### P1 — Incertidumbre artificialmente estrecha con dos cierres

La función [expectancy_uncertainty](C:/Users/mario/Desktop/Trading/backend/app/training.py:491) usa bloques circulares de longitud mínima dos. Con `[-1, 3]`, la reproducción devuelve media 1 e intervalo `[1, 1]`: cada bloque incluye ambas observaciones y la media no cambia.

El mínimo de operaciones de otros filtros evita algunas promociones, pero el intervalo mostrado sigue siendo engañoso. **Aceptación propuesta:** no presentar inferencia con tan pocos bloques independientes; separar muestras descriptivas de intervalos utilizables y añadir regresiones de muestras pequeñas, dependencia y distribuciones asimétricas.

### P1 — El replay de investigación no equivale a la política operativa

Los [planes A1–A4](C:/Users/mario/Desktop/Trading/backend/app/research_plans.py:23) tienen horizontes y geometrías propios. A3 utiliza fuerza relativa diaria de 60 barras; la mesa utiliza otra selección y otras salidas. El [comparador v9/v10](C:/Users/mario/Desktop/Trading/backend/app/exit_experiments.py:195) declara límites de OHLC y de equivalencia. El replay antiguo tampoco sustituye al motor completo.

**Aceptación propuesta:** compartir funciones puras de transición de estado entre replay y paper; comprobar paridad por evento, costes, gaps, parciales, filtros, pausas y capital disponible. Conservar la distinción entre replay diagnóstico de salidas y validación de la estrategia completa.

### P2 — L1 y 50 eventos mejoran la memoria, pero no certifican el aprendizaje

En la reproducción aislada del [actualizador](C:/Users/mario/Desktop/Trading/backend/app/learning.py:417), dos features idénticas conservan pesos idénticos de aproximadamente 0,406 tras 100 actualizaciones con L1. La penalización no garantiza eliminar duplicados. Zou y Hastie describen precisamente comportamientos de agrupación con predictores correlacionados. [Artículo original](https://academic.oup.com/jrsssb/article/67/2/301/7109482).

**Aceptación propuesta:** agrupar confirmaciones por la información que aportan, eliminar duplicados exactos, normalizar usando solo entrenamiento y medir ablando familias de features. La ventana de 50 cierres evita olvidar por baja frecuencia; esos cierres pueden pertenecer a un único régimen y no constituyen 50 observaciones independientes.

## 4. Método para demostrar una ventaja neta

### Comparadores y objetivo

Cada investigación debería comparar efectivo, comprar y mantener, una regla simple y esa misma regla con el overlay, con comisiones y exposición comparables. Si se reduce exposición, conviene distinguir ahorro de riesgo, menor participación en subidas y mejora de selección. No basta con que una estrategia gane durante un mercado alcista.

El objetivo es la expectativa y la distribución del resultado **después de costes**, no el porcentaje de acierto:

`expectativa neta = frecuencia de ganancias × ganancia media neta − frecuencia de pérdidas × pérdida media neta absoluta`.

Si ambas medias ya incluyen costes, no se vuelven a restar comisiones. Deben publicarse drawdown, pérdidas de cola, exposición, rotación, incertidumbre y comparación con el benchmark. Un profit factor sin pérdidas observadas no es una certeza infinita: su denominador todavía no está estimado.

### Datos, particiones y búsqueda

1. Congelar hipótesis, parámetros, fuente, universo histórico, ejecución, costes, objetivo y criterios antes de evaluar.
2. Mantener tiempos separados para el hecho, publicación, recepción, cálculo, decisión, envío y fill. Noticias fechadas después de decidir quedan fuera.
3. Usar validación temporal con purga por duración efectiva de etiquetas/posiciones, embargo explícito y un tramo final reservado. Las reglas congeladas pueden evaluarse por bloques; si se ajusta un modelo, todo ajuste y transformación se hacen dentro del entrenamiento.
4. Mantener el contador total de intentos y los resultados fallidos. Separar familias permite investigación descriptiva, pero no convierte un mismo periodo en evidencia nueva al cambiarle el nombre.
5. Corregir la búsqueda entre alternativas. El trabajo de Novy-Marx explica el problema de seleccionar combinaciones de señales; DSR y PBO cubren aspectos diferentes de selección y sobreajuste. Ninguno es una probabilidad garantizada de beneficio futuro. [Múltiples señales](https://www.nber.org/papers/w21329), [DSR](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf), [PBO](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
6. Someter el conjunto a fricciones normales y adversas, retrasos, gaps, datos ausentes, fills parciales y fallos. Publicar tanto el rerun con otra política de ejecución como la repricing del mismo conjunto de operaciones: responden a preguntas distintas.
7. Reservar seguimiento prospectivo y decisiones de promoción manuales. No modificar reglas después de cada pérdida y seguir llamando al historial una sola cohorte.

Los mínimos de 20, 30 o 50 observaciones son controles operativos, no certificaciones estadísticas. Como ilustración matemática, con desviación de 1,2 R y observaciones independientes, un semiancho normal aproximado de 0,1 R al 95 % exigiría `(1,96 × 1,2 / 0,1)^2 ≈ 554` observaciones. No es un cálculo de potencia ni una recomendación universal de tamaño muestral; dependencia y colas alteran esa aproximación.

### Medición específica de IA y noticias

Mediría la diferencia incremental de PnL neto y riesgo sobre las mismas oportunidades, sin excluir desacuerdos ni interpretar ausencia de datos como aprobación. Guardaría modelo, prompt, respuestas, fuentes y su disponibilidad real. Las fuentes duplicadas no cuentan como confirmaciones independientes.

Separaría las contribuciones: selección de activos, veto de noticias, lectura de derivados, tamaño y salidas. Si se cambian simultáneamente, no se identifica cuál ayudó. Las reglas de short squeeze, ADX 28, CVD −0,20 y parcial a 1,8 R son hipótesis congelables; **no se han demostrado óptimas por haber sido implementadas**.

## 5. La restricción económica del capital pequeño

En el código cripto, comisión `0,001` y slippage `0,0002` por lado implican aproximadamente **24 puntos básicos por ida y vuelta**, incluso antes de añadir spread, en un ejemplo sin variación de precio. Es aritmética de los supuestos de [desk.py](C:/Users/mario/Desktop/Trading/backend/app/desk.py:31), no la tarifa real de la cuenta. Se debe evitar duplicar el spread cuando los precios ya son bid/ask.

Las comisiones reales dependen de cuenta, símbolo y circunstancias; Binance documenta cómo consultarlas. Los lotes y nominales mínimos también condicionan entradas y parciales. Los streams de trades/libro aportan observaciones de mercado, pero no prueban nuestra prioridad en cola. [Comisiones](https://developers.binance.com/en/docs/products/spot/faqs/commission_faq), [filtros](https://developers.binance.com/en/docs/products/spot/filters), [streams](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams).

También hay costes fijos. **Ejemplo hipotético:** con 100 EUR y 15 EUR mensuales de costes incrementales de datos/infraestructura/IA, haría falta un 15 % mensual de resultado de trading solo para cubrirlos, antes de otros cargos. No afirmo que ese sea el coste real del proyecto ni un objetivo razonable. Si una suscripción ya existe para otros usos, se debe atribuir su coste incremental con escenarios transparentes.

El perfil local permite hasta seis posiciones, riesgo objetivo del 1,5 % por entrada y 6 % agregado en la mesa; los controles globales pueden restringirlo más. Es capacidad técnica, no evidencia de ventaja. No aumentaría riesgo ni frecuencia para intentar cubrir costes fijos o alcanzar un beneficio diario. Primero mediría la ventaja con presupuestos congelados y sin modificar los límites protegidos.

## 6. Plan de trabajo priorizado

| Fase | Entregable | Condición para avanzar |
|---|---|---|
| 0. Integridad de la medición | Resolver identidad candidata/auditoría, distinguir código de servicio y corregir incertidumbre con muestras pequeñas | Reproducciones anteriores convertidas en pruebas de integración; cierre conciliado de extremo a extremo. |
| 1. Base económica | Una hipótesis diaria de tendencia y otra de fuerza relativa; historia suficiente para distintos regímenes y universo fechado | Comparadores, costes y protocolo congelados; resultado descriptivo completo, incluidos rechazos. |
| 2. Política y ejecución | Comparación v9/v10 en replay común; A5 con todos los intentos maker/taker | Paridad documentada y resultados que no dependan de fills escogidos o fricciones optimistas. |
| 3. IA incremental | Libros shadow independientes para reglas, pulse, commander e IA completa; noticias punto en el tiempo | Mejora neta estimada con incertidumbre y cobertura de decisiones discordantes. |
| 4. Seguimiento futuro | Una cohorte estable, decisiones congeladas y revisión predefinida | Suficiente evidencia independiente; si no aparece ventaja, conservar efectivo/sombra. |

Las fases iniciales son trabajo de ingeniería e investigación; el seguimiento depende de la frecuencia real y puede requerir meses. Un periodo de 60–90 días puede servir como primera observación operativa, pero no certifica estabilidad ni obliga a acumular operaciones artificialmente. No se han creado automatizaciones ni encargado trabajos a otros chats en esta revisión.

## 7. Bibliografía primaria y límites

1. [Hurst, Ooi y Pedersen — A Century of Evidence on Trend-Following Investing, 2017](https://www.aqr.com/insights/research/journal-article/a-century-of-evidence-on-trend-following-investing). Historia multi-activo; no validación de v10.
2. [Liu y Tsyvinski — Risks and Returns of Cryptocurrency, NBER 2018; publicación posterior en RFS](https://www.nber.org/papers/w24877). Momentum y atención en su muestra.
3. [Liu, Tsyvinski y Wu — Common Risk Factors in Cryptocurrency, NBER 2019; Journal of Finance 2022](https://www.nber.org/papers/w25882). Factores cripto históricos.
4. [Moreira y Muir — Volatility-Managed Portfolios, NBER 2016; Journal of Finance 2017](https://www.nber.org/papers/w22208). Gestión de volatilidad; transferencia a cripto por comprobar.
5. [Gu, Kelly y Xiu — Empirical Asset Pricing via Machine Learning, NBER 2018; RFS 2020](https://www.nber.org/papers/w25398). Panel de acciones, no cierres de un pequeño bot.
6. [Frazzini, Israel y Moskowitz — Trading Costs of Asset Pricing Anomalies, documento de trabajo 2012](https://pages.stern.nyu.edu/~afrazzin/pdf/Trading%20Cost%20of%20Asset%20Pricing%20Anomalies%20-%20Frazzini%2C%20Israel%20and%20Moskowitz.pdf). Costes institucionales, no tarifa minorista garantizada.
7. [Novy-Marx — Backtesting Strategies Based on Multiple Signals, NBER 2015](https://www.nber.org/papers/w21329). Sesgo de combinar y seleccionar señales.
8. [Bailey y López de Prado — The Deflated Sharpe Ratio, 2014](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf). Selección y no normalidad.
9. [Bailey, Borwein, López de Prado y Zhu — The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf). Documento de los autores y marco CSCV; no equivalente a purga temporal de etiquetas.
10. [López-Lira y Tang — Can ChatGPT Forecast Stock Price Movements?, versión 2025](https://arxiv.org/abs/2304.07619). Reacción no negociable y deriva posterior; no retorno directamente transferible a cripto.
11. [Glasserman y Lin — Assessing Look-Ahead Bias in Stock Return Predictions Generated By GPT Sentiment Analysis, preprint 2023](https://arxiv.org/abs/2309.17322). Contaminación y anonimización.
12. [AI’s predictable memory in financial analysis — Economics Letters, 2025](https://www.sciencedirect.com/science/article/pii/S0165176525004392). Memorización y fuga temporal. Se consultaron resumen y metadatos; el acceso al texto completo fue limitado.
13. [Schmeling, Schrimpf y Todorov — Crypto carry, BIS 2023](https://www.bis.org/publications/working-paper-1087-crypto-carry). Base, segmentación y riesgos de arbitraje.
14. [Nagel — Evaporating Liquidity, NBER 2011; RFS 2012](https://www.nber.org/papers/w17653). Reversión y provisión de liquidez en acciones.
15. [Gatev, Goetzmann y Rouwenhorst — Pairs Trading, NBER 1999; RFS 2006](https://www.nber.org/papers/w7032). Evidencia histórica de pares de acciones.
16. [Avellaneda y Stoikov — High-frequency trading in a limit order book, 2008](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf). Modelo de inventario y ejecución.
17. [Zou y Hastie — Regularization and Variable Selection via the Elastic Net, 2005](https://academic.oup.com/jrsssb/article/67/2/301/7109482). Regularización y predictores correlacionados.
18. [Binance — Commission Rates](https://developers.binance.com/en/docs/products/spot/faqs/commission_faq). Verificación de costes de cuenta; ejemplos documentales ficticios.
19. [Binance — Filters](https://developers.binance.com/en/docs/products/spot/filters). Cantidad, precio y nominal.
20. [Binance — Spot WebSocket Market Streams](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams). Observaciones de mercado y tiempos.

Los trabajos antiguos explican mecanismos y métodos; no se presentan como rendimientos disponibles hoy. Las afirmaciones sobre el proyecto proceden de la captura y del código; las prioridades y criterios propuestos son juicio de esta revisión. No se ha realizado un nuevo concurso de estrategias, una prueba de fills reales ni una validación prospectiva de v10 durante esta entrega.
