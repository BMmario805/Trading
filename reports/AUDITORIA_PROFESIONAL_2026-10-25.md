# Auditoría profesional y plan de investigación de Mesa Siete

Fecha: 25 de octubre de 2026. Corte contable: 12:57:12 CET, Europe/Madrid.

## Dictamen

**El proyecto tiene una base de software útil, pero la evidencia examinada todavía no permite declararlo rentable ni atribuir una ventaja monetaria a la IA.** La prioridad es corregir contratos de ejecución, hacer que la validación reproduzca la política operativa y medir una estrategia sencilla sobre datos realmente nuevos. Aumentar frecuencia, tamaño o número de agentes no resuelve una expectativa neta negativa o desconocida.

Recomiendo concentrar la siguiente fase en cripto spot líquido, estrategias de tendencia/fuerza relativa de baja rotación y una comparación explícita con reglas sin IA. Forex, acciones, scalping y market making necesitan su propia evidencia; dispersar ahora la investigación entre todas las familias consume muestra y multiplica las oportunidades de sobreajuste. Esta prioridad es una decisión de ingeniería e investigación para este proyecto, no una predicción de qué mercado dará más beneficios.

La revisión ha producido hallazgos y propuestas. No se han corregido los motores de trading, cambiado límites, armado el enjambre ni enviado órdenes. Los encargos a Grok son de investigación y revisión. No hay una fecha ni una tasa de beneficios garantizada.

## 1. Qué se ha revisado y cómo interpretar los números

Se contrastaron los motores de mesas, bots, entrenamiento, metamodelo, economía, ejecución MT5, riesgo agregado, simulación maker, datos, coordinación IA, despliegue y reportes históricos. Tres agentes locales revisaron por separado validación, ejecución y evidencia. Las reproducciones de fallos emplearon funciones extraídas y dobles inertes: no operaron sobre cuentas.

Los libros SQLite se leyeron en modo de solo lectura y transacción por base. El corte es consistente dentro de cada libro, pero no es una transacción atómica entre todas las bases. La aplicación seguía funcionando: cifras posteriores pueden variar. El detalle agregado está en [audit-evidence-2026-10-25.json](C:/Users/mario/Desktop/Trading/reports/audit-evidence-2026-10-25.json).

Estado del código revisado: HEAD `a962858f4cee76541f167b66cc79c803197eba50`. Ya existían cambios locales en README, `ai_swarm.py`, `.grok/`, `specialist_team.py` y sus pruebas; se preservaron. El informe corresponde a ese árbol de trabajo, no exclusivamente al commit.

Validaciones de software realizadas en esta revisión:

- Backend: **480 pruebas y 105 subpruebas pasadas**; aviso de deprecación de Starlette/httpx.
- Panel: compilación TypeScript/Vite correcta; aviso por un paquete de salida mayor de 500 kB.
- Análisis del panel: sin errores; tres avisos sobre exportaciones para recarga y dependencias de efectos de React.

Estas comprobaciones prueban contratos de software existentes. No prueban rentabilidad, seguridad de todos los caminos ni equivalencia económica entre simulación y ejecución. No se ha hecho una prueba de penetración, un ensayo real de recuperación del despliegue ni validación de fills reales del exchange.

### Resultados observados, separados por libro

| Libro | Capital virtual de referencia | Cierres examinados | PnL cerrado registrado | Profit factor | Valoración persistida |
|---|---:|---:|---:|---:|---:|
| Cripto | 112,0953 USDT, equivalentes iniciales a 100 EUR virtuales | 54 | −24,2532 USDT | 0,1821 | 87,8422 USDT; sin abiertas |
| Forex | 100 USD | 13 | −0,06556 USD | 0,4801 | 99,9344 USD; sin abiertas |
| Acciones | 100 USD | 5 | +0,000745 USD | 1,0023 | 100,1949 USD; una abierta heredada |
| Laboratorio | 700 EUR, siete libros de 100 EUR | 83 con costes netos explícitos | −53,0761 EUR | 0,0952 | Equity actual no reconstruible fielmente solo de SQLite |
| MT5 DEMO | 100 EUR iniciales y 2.400 EUR de asignaciones virtuales posteriores | 1 posición cerrada | −2,22 EUR | 0 | 2.497,78 EUR; sin abiertas |

Profit factor es ganancias cerradas divididas por pérdidas cerradas en valor absoluto; no es rentabilidad porcentual. No se suman divisas distintas ni cuentas virtuales independientes para presentar una rentabilidad conjunta.

Matices que cambian la interpretación:

- En cripto, **37 cierres tienen versión `net-costs-v1` explícita: −19,2603 USDT**. Otros 17 son históricos sin esa versión; no debe describirse todo el conjunto como una cohorte homogénea de costes verificados.
- La política cripto actual, **`desk-rs-chandelier-1h-v8`**, comenzó hoy a las **12:24:57 CET y tiene cero cierres**. Las pérdidas anteriores no son su historial. Tampoco hay evidencia para llamarla rentable.
- Forex y acciones usan actualmente `desk-cash-aggressive-v2`, con cero cierres de esa política. La posición abierta de acciones es heredada. Su cotización persistida más antigua es del 21/10: una actualización del panel no certifica una valoración de mercado fresca.
- El balance del laboratorio es 642,9150 EUR y conserva ocho posiciones abiertas. Hay además 17 cierres antiguos, separados, por −4,0089 EUR. Balance y equity no son intercambiables: falta la valoración viva de esas posiciones para calcular equity actual.
- El laboratorio actual `engineVersion=7` + `aggressive-capacity-v4` tiene Flow: cinco cierres y +0,1579 EUR; Scalper: dos y −3,9264; Swing: dos y −7,1628; Trend: tres y −0,02465. Las otras familias tienen cero. Son muestras insuficientes para elegir un ganador.
- MT5 estaba conciliado con diferencia cero a las 12:57:10 CET. El aumento de asignación virtual a 2.500 EUR no es beneficio ni depósito real. Todos los libros examinados corresponden a paper o DEMO.

### Qué dice la validación histórica

El [estudio del 24/10](C:/Users/mario/Desktop/Trading/reports/grok-only-research-20261024.json) evaluó **96 combinaciones en ocho datasets; ninguna resultó elegible**. Solo LINK/4h `momentum-vol-12` llegó al tramo final: siete cierres, +1,81 USDT por referencia de 100, +1,40 con fricciones duplicadas y límite inferior del intervalo de expectativa del 95% de **−0,2682 USDT**. No cumple el mínimo de 20 cierres ni la incertidumbre exigida. No es una estrategia aprobada.

Las 132 validaciones guardadas del laboratorio son motor v7 y **ninguna está aplicada**. Las causas: 84 sin configuración rentable en selección; 39 por muestra, consistencia, DSR, vecindad o costes; cinco por usar proxies; cuatro por historia insuficiente. MT5 no conserva una validación aplicada v7; sí una antigua v3. Los siete metamodelos tienen entre dos y veinte ejemplos, por debajo de su umbral operativo de 30.

Los informes antiguos y las pruebas agresivas son exploración. Las ventanas solapan investigaciones previas y algunas variantes se eligieron después de observar resultados. Un campo `ok=true` significa que una evaluación se pudo ejecutar; `success=true` de un especialista significa que su propuesta satisface el contrato. Ninguno significa que exista ventaja económica.

## 2. Hallazgos que bloquean una evaluación profesional

P0 significa que propongo resolverlo antes de escalar o considerar capital real; P1, antes de promover una estrategia; P2, mejora posterior. No implica que se haya demostrado una pérdida real causada por cada defecto.

### P0.1 — Una pausa puede terminar seguida de una entrada paper pendiente

El último control de pausa/límite diario está en [desk.py:1040](C:/Users/mario/Desktop/Trading/backend/app/desk.py:1040). Después hay una espera de derivados en [desk.py:1113](C:/Users/mario/Desktop/Trading/backend/app/desk.py:1113), y la posición se añade en [desk.py:1171](C:/Users/mario/Desktop/Trading/backend/app/desk.py:1171) sin una nueva comprobación atómica.

Reproducción aislada: durante la espera se activaron `enabled=False`, `dailyStopped=True` y `flatten=True`; la función terminó con estado ejecutado y una posición nueva. No fue una operación real.

**Solución y aceptación:** tras la última espera, bloqueo breve y revalidación de pausa, generación de la decisión, liquidación solicitada, cupos, caja, riesgo y cotización. Prueba que pause durante la espera y compruebe cero entradas y liberación de reservas.

### P0.2 — Un cierre MT5 rechazado puede quedar sin reintento

[mt5_demo.py:153](C:/Users/mario/Desktop/Trading/backend/app/mt5_demo.py:153) deduplica una clave existente con independencia del resultado. El cierre temporal reutiliza `close:ticket:volume` en [mt5_demo.py:302](C:/Users/mario/Desktop/Trading/backend/app/mt5_demo.py:302). Una respuesta transitoria de rechazo deja bloqueados intentos posteriores con la misma clave.

Reproducción aislada: primer envío rechazado por requote; segundo intento devuelve `None`; contador de envíos del doble sigue en uno.

**Solución y aceptación:** separar decisión e intentos. Reintentar un rechazo confirmado con nueva cotización y conciliación previa; conservar el bloqueo de respuestas inciertas. Probar rechazo, posterior éxito, ejecución parcial, reinicio y ausencia de doble cierre.

### P0.3 — Las reservas de riesgo desaparecen al reiniciar

Las reservas globales viven en RAM en [portfolio_risk.py:17](C:/Users/mario/Desktop/Trading/backend/app/portfolio_risk.py:17). MT5 persiste el token del intento, pero no reconstruye su importe antes de volver a admitir riesgo. Una orden incierta puede dejar de consumir presupuesto agregado después de un reinicio, aunque su símbolo permanezca bloqueado.

**Solución y aceptación:** reservas persistentes ligadas al intento, recuperación anterior a habilitar entradas y conciliación de posición/rechazo/cierre. Ensayo de fallo entre reserva, envío, acuse y publicación; en cada punto, el presupuesto debe conservarse sin duplicar ni liberar riesgo desconocido.

### P0.4 — Los cálculos de riesgo MT5 no comparten la misma definición monetaria

El tamaño nuevo contempla comisión conocida en [mt5_gateway.py:117](C:/Users/mario/Desktop/Trading/backend/app/mt5_gateway.py:117). El riesgo existente la omite en [mt5_gateway.py:110](C:/Users/mario/Desktop/Trading/backend/app/mt5_gateway.py:110); la publicación global utiliza pérdida al stop × 1,1 en [mt5_demo.py:446](C:/Users/mario/Desktop/Trading/backend/app/mt5_demo.py:446).

Una prueba contractual sintética con comisión alta admitió propuestas cuyo riesgo local total era 28,8 para un límite de 20, mientras se publicarían 15,84. No demuestra que esa sea la comisión de la cuenta ni que el control global admita realmente esas tres órdenes; demuestra una inconsistencia.

**Solución y aceptación:** una función compartida para pérdida al stop, costes pendientes y moneda, evitando volver a contar comisiones ya pagadas. El tamaño, las posiciones, las reservas y el auditor deben devolver el mismo riesgo contractual para el mismo escenario.

### P0.5 — La mesa cripto puede quedar fuera del presupuesto global si falta financiación/conversión

La inicialización permite una mesa habilitada sin `funding` en [desk.py:255](C:/Users/mario/Desktop/Trading/backend/app/desk.py:255). El recolector la excluye sin conversión EUR en [portfolio_risk.py:60](C:/Users/mario/Desktop/Trading/backend/app/portfolio_risk.py:60), y la entrada consulta el presupuesto solo si existe esa tasa en [desk.py:1119](C:/Users/mario/Desktop/Trading/backend/app/desk.py:1119).

Es un camino de instalación nueva o estado heredado; no afirmo que la sesión actual carezca de financiación.

**Solución y aceptación:** estado explícito no preparado y entradas bloqueadas hasta tener sesión, moneda y conversión válidas, o un presupuesto alternativo declarado en moneda nativa. Probar arranque vacío, estado antiguo y conversión vencida.

### P1.1 — FLOW no utiliza el mismo marco superior en entrenamiento y laboratorio

[strategy.py:74](C:/Users/mario/Desktop/Trading/backend/app/strategy.py:74) fija `TREND_STEP['flow']=3600`; [training.py:682](C:/Users/mario/Desktop/Trading/backend/app/training.py:682) lo utiliza. El laboratorio hereda 4h en [bots.py:271](C:/Users/mario/Desktop/Trading/backend/app/bots.py:271), y la función FLOW presupone ese marco. Una reproducción con 2.000 velas sintéticas cambió 14 puntuaciones y produjo 251 frente a 239 señales. Los tests de causalidad existentes construyen directamente el marco correcto y no atraviesan esta discrepancia.

**Solución y aceptación:** una definición única del intervalo. Para el mismo histórico cerrado, entrenamiento y decisión operativa deben producir la misma serie y las mismas entradas, incluido el recorrido real de agregación. Revalidar después; no trasladar estadísticas del recorrido anterior.

### P1.2 — La política operativa contiene módulos que no se reproducen históricamente

Derivados públicos pueden modificar la puntuación hasta 0,05 en [strategy.py:94](C:/Users/mario/Desktop/Trading/backend/app/strategy.py:94), llamados desde [bots.py:807](C:/Users/mario/Desktop/Trading/backend/app/bots.py:807). El replay base calcula `score_series` en [training.py:693](C:/Users/mario/Desktop/Trading/backend/app/training.py:693). Tampoco reproduce exactamente veto contextual, metamodelo y dimensionado posteriores.

**Solución y aceptación:** registro de entradas con fecha real de disponibilidad y replay de la política completa. Hasta disponer de esa historia, esos módulos deben tener evaluación en sombra y atribución separada. Validar la base no valida automáticamente la base más IA, derivados y cambios de tamaño.

### P1.3 — Cooldowns y consumo de señales difieren

[training.py:687](C:/Users/mario/Desktop/Trading/backend/app/training.py:687) redondea esperas a velas y el simulador utiliza una espera fija tras pérdidas. El vivo aplica espera exponencial por racha en [bots.py:1230](C:/Users/mario/Desktop/Trading/backend/app/bots.py:1230). Para Squeeze/Flow, el replay espera al menos una vela horaria; el vivo espera 300 segundos tras ganar y 2.400 tras la primera pérdida. El escaneo puede reutilizar la misma vela cerrada.

**Solución y aceptación:** política de espera común y reloj de eventos, o una decisión por vela compartida por ambos recorridos. Comparar secuencias pérdida/pérdida/ganancia, trailing, parciales y cierre intrabar, no solo puntuaciones de entrada.

### P1.4 — El tramo final se vuelve a consultar dentro de reentrenamientos móviles

[training.py:462](C:/Users/mario/Desktop/Trading/backend/app/training.py:462) divide el histórico recibido y [learning.py:97](C:/Users/mario/Desktop/Trading/backend/app/learning.py:97) repite el entrenamiento cada 4–12 horas. No hay un registro inmutable que impida utilizar repetidamente fechas ya consultadas. Guardar el último `bot_params` o prohibir sobrescribir un nombre de reporte no resuelve la reutilización.

Además, entrenamiento exige ocho cierres finales y dos de tres bloques positivos; investigación exige veinte, tres bloques e intervalo inferior positivo. El DSR actual corrige la rejilla de una llamada, no toda la búsqueda del proyecto.

**Solución y aceptación:** registro acumulativo de hipótesis, datos, fechas, versión, parámetros, costes, búsquedas y consultas del tramo final. Congelar antes de recibir datos futuros; marcar el histórico ya visto como desarrollo. Un único contrato de promoción por producto y un historial completo de intentos. La necesidad de corregir selección y múltiples ensayos está documentada por [Bailey y colaboradores, PBO](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) y [Bailey y López de Prado, DSR](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).

### P1.5 — Muestra operativa del metamodelo no equivale a ventaja predictiva

[learning.py:84](C:/Users/mario/Desktop/Trading/backend/app/learning.py:84) usa 30 ejemplos como madurez; [learning.py:369](C:/Users/mario/Desktop/Trading/backend/app/learning.py:369) permite hasta 1,25× bajo ciertas condiciones. La salida declara correctamente que no es una probabilidad calibrada. No existe una prueba económica posterior que justifique esa ampliación.

**Solución y aceptación:** predecir, registrar, cerrar y solo entonces aprender; evaluar base frente a filtro con riesgo comparable. Conservar candidatos rechazados y resultados contrafactuales. No permitir aumento por una puntuación no calibrada. Si se muestra una probabilidad, comprobar calibración en datos futuros, además de beneficio neto; acertar dirección no basta.

### P1.6 — Identidad de operaciones y filtros deben ser comunes

El warm start deduplica lotes completos, no cada operación, en [learning.py:432](C:/Users/mario/Desktop/Trading/backend/app/learning.py:432). Lotes `[A,B]` y `[B,C]` pueden enseñar B dos veces. Es correcto que esto no aumente el contador de madurez del vivo, pero sí puede sobreponderar el histórico.

Por otra parte, [desk.py:204](C:/Users/mario/Desktop/Trading/backend/app/desk.py:204) no comprueba `NOTIONAL.maxNotional`, mientras la ruta maker sí. Una reproducción produjo nominal 160,032 con máximo 5.

**Solución y aceptación:** identidad individual de cada operación histórica y recibos por ejemplo; filtros compartidos maker/taker según tipo de orden y sus banderas de aplicabilidad. Probar lotes parcialmente solapados y límites después de cada redimensionado.

### P1.7 — Datos y exposición necesitan identidad y edad reales

[portfolio_risk.py:20](C:/Users/mario/Desktop/Trading/backend/app/portfolio_risk.py:20) clasifica factores por nombre literal. Sufijos de bróker pueden cambiar `XAUUSD` de oro a cripto; `US500` también cae en el grupo residual cripto. No se ha comprobado que la cuenta actual utilice esos alias.

El agregado conserva nominal de entrada en vez de exposición actual. El libro utiliza recepción local en [book_stream.py:148](C:/Users/mario/Desktop/Trading/backend/app/book_stream.py:148); la caché de derivados no valida por separado la edad de todos los datos de origen en [derivatives_flow.py:119](C:/Users/mario/Desktop/Trading/backend/app/derivatives_flow.py:119).

**Solución y aceptación:** catálogo canónico de instrumentos y contratos, factores verificados, moneda y multiplicadores; desconocidos bloqueados para dimensionar. Exposición marcada a mercado, límites por factor y escenarios conjuntos de caída. Guardar hora del evento, disponibilidad, recepción y decisión, y bloquear nuevas entradas ante datos insuficientes o vencidos.

## 3. Métodos y estrategias: qué merece probarse aquí

La literatura justifica hipótesis, no traslada una rentabilidad publicada al código. Las conclusiones siguientes son una selección razonada para los recursos, costes, instrumentos y problemas observados.

| Prioridad de investigación | Familia | Por qué tiene sentido | Prueba necesaria y límites |
|---|---|---|---|
| Alta | Tendencia sencilla 4h/1D, spot largo/caja | Menos rotación y una señal comprensible que permite auditar costes y decisiones | Datos causales, universo líquido, comparación con mantener el activo y con efectivo; puede sufrir pérdidas prolongadas en mercados laterales |
| Alta | Fuerza relativa entre activos líquidos | Permite probar si seleccionar aporta algo sobre una exposición pasiva | Ranking con universo disponible en cada fecha; controlar beta y concentración; momentum semanal de la literatura no valida ranking 24/72h |
| Alta, como control | Dimensionado por volatilidad y presupuesto conjunto | Ajusta exposición al riesgo observado y evita que varias altcoins se traten como apuestas independientes | Volatilidad estimada solo con pasado, límites duros y estrés; evitar apalancar automáticamente periodos tranquilos |
| Media | Régimen sencillo como filtro de la misma base | Puede reducir exposición en escenarios adversos | Comparar base frente a base+filtro con menos parámetros; demostrar mejora además de pasar más tiempo en caja |
| Media, condicionada | Reversión a la media en mercados líquidos y rango | Hipótesis distinta de tendencia que puede aportar diversificación | Debe sobrevivir costes y crisis; no comprar caídas indefinidamente ni mezclarla con martingala; no se identificó evidencia suficiente para promover la implementación actual |
| Media, como ejecución | Maker frente a taker | Puede mejorar precio de ejecución de una señal ya útil | Medir no ejecución, cola, parciales, cancelación, selección adversa y oportunidad perdida; maker no implica tarifa inferior |
| Media/baja, primero sombra | Noticias y metamodelo como complemento | La IA puede ordenar información y detectar riesgos concretos | Valor incremental frente a la misma regla sin IA, con noticias disponibles entonces y latencia real; excluir reacción de precio ya ocurrida |
| Posterior | Árboles o modelos lineales regularizados | Permiten aprender interacciones sobre datos tabulares con referencias claras | Solo tras obtener datos, etiquetas y validación fiables; comparar con la regla simple; no empezar por el modelo más grande |
| Posterior, producto distinto | Cash-and-carry spot/futuros | Hipótesis de base y financiación distinta de predecir precio | Necesita cobertura, margen, financiación, contraparte y contratos; el motor spot actual no lo implementa |
| Baja ahora | Scalping, market making continuo, RL y gran búsqueda de indicadores | Exigen microestructura, ejecución y muestras que hoy no están demostradas | Reconsiderar solo si se miden y superan esas restricciones; no usar velocidad o complejidad como argumento de rentabilidad |

La tendencia cuenta con evidencia histórica en futuros y forwards diversificados: [Hurst, Ooi y Pedersen, 2017](https://www.aqr.com/insights/research/journal-article/a-century-of-evidence-on-trend-following-investing). La investigación de [Liu, Tsyvinski y Wu](https://www.nber.org/papers/w25882.pdf) identifica factores de mercado, tamaño y momentum en cripto. Esos resultados motivan las primeras dos familias, pero no demuestran que spot solo largo, velas 4h o la nueva política v8 sean rentables.

[Moreira y Muir](https://www.nber.org/papers/w22208) estudian reducción de riesgo cuando aumenta la volatilidad en carteras de factores. Propongo probar ese control con topes; no extrapolar sus resultados como permiso para multiplicar tamaño.

[Zaremba y colaboradores, 2021](https://www.sciencedirect.com/science/article/pii/S1057521921002349) encuentran reversión diaria dependiente de iliquidez, mientras las monedas más negociables de su muestra muestran momentum diario. Esto impide asumir que una anomalía encontrada entre miles de monedas pueda explotarse en nuestros pares líquidos o con tamaños y costes reales. La prioridad de reversión queda condicionada a una prueba propia.

Para aprendizaje automático, [Gu, Kelly y Xiu](https://doi.org/10.1093/rfs/hhaa009) encuentran utilidad en interacciones de momentum, liquidez y volatilidad sobre un gran panel histórico de acciones. La muestra y el horizonte son muy distintos de nuestros pocos cierres. Su trabajo respalda evaluar modelos con regularización y referencias; no demuestra que una conversación entre LLM produzca beneficios intradía.

La prioridad baja de market making se deriva de las limitaciones del proyecto y de los riesgos de inventario y ejecución descritos en [Avellaneda y Stoikov](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf). Un fill simulado no demuestra prioridad real en la cola. La simulación actual es explícitamente una aproximación, [maker_simulation.py:93](C:/Users/mario/Desktop/Trading/backend/app/maker_simulation.py:93).

El [BIS, Crypto carry, revisión octubre de 2025](https://www.bis.org/publ/work1087.pdf) documenta base y riesgos de margen/liquidación. Lo reservaría para investigación posterior, sin denominarlo arbitraje libre de riesgo ni prometer sus retornos históricos.

## 4. Cómo sacar partido de la IA sin confundir explicaciones con resultados

En el modo de suscripción actual, pulso y commander utilizan LLM; Flow y Quant-Risk son funciones deterministas locales, y ChatGPT hace revisiones más lentas: [ai_swarm.py:1381](C:/Users/mario/Desktop/Trading/backend/app/ai_swarm.py:1381). No hay cuatro modelos independientes votando por entrada. Los pesos y umbrales son decisiones de diseño; no encontré calibración económica que los valide. SQLite conserva tres decisiones de esa coordinación, todas en sombra y sin envío.

Propongo estas funciones, evaluables por separado:

1. **Radar:** localizar acontecimientos e investigaciones con URL original, fecha de publicación y hora de disponibilidad. Una noticia antigua que reaparece no es un nuevo catalizador.
2. **Verificador:** confirmar contenido y origen, detectar correcciones y contradicciones y distinguir fuentes independientes de republicaciones. Dos dominios no equivalen a dos confirmaciones editoriales.
3. **Investigador:** redactar una hipótesis y una prueba antes de consultar resultados; registrar también ideas fallidas. Su objetivo es reducir el tiempo para refutar o confirmar una propuesta.
4. **Revisor:** reproducir fallos y detectar diferencias entre código operativo y simulado, sin decidir tamaños ni saltarse controles.
5. **Coordinador:** consolidar desacuerdos, evidencias y siguiente decisión. El consenso de varios agentes que comparten modelo o fuentes no es una muestra estadística independiente.

El estudio de [Lopez-Lira y Tang, versión de octubre de 2025](https://arxiv.org/abs/2304.07619) distingue la reacción inicial **no operable** de la deriva posterior y encuentra que la ventaja disminuye con mayor adopción de LLM. No corresponde usar su cifra próxima al 90% sobre reacciones iniciales como tasa de acierto de un bot ejecutable. [Glasserman y Lin](https://arxiv.org/abs/2309.17322) explican problemas de contaminación temporal y del conocimiento previo de empresas al probar sentimiento retrospectivo.

**Diseño de atribución:** registrar cada candidato de la regla, incluidos los vetados, con versión de código/modelo/prompt, datos disponibles, hora, respuesta, latencia, decisión y coste. Mantener carteras sombra comparables: reglas; reglas+pulso; reglas+commander; reglas+filtro de régimen. Comparar diferencia de PnL neto, riesgo y exposición con el mismo modelo de ejecución, agrupando observaciones dependientes. Un contrafactual simulado sigue siendo una estimación, no un fill ejecutado.

Si la IA aumenta latencia, bloquea buenas entradas o aporta una mejora menor que sus costes, se retira ese módulo de la ruta de entrada y puede seguir ayudando en investigación. Las salidas de protección deben seguir funcionando de manera independiente aunque falle el modelo.

## 5. Siete experimentos concretos antes de ampliar la búsqueda

Son propuestas para preregistrar; no se han ejecutado como parte de esta auditoría. No equivalen a los E1–E7 redactados por Grok, aunque coinciden varias familias. Cada variante y cada inspección de resultados debe contar en el registro acumulado.

| ID | Hipótesis y contraste | Elemento que debe permanecer fijo | Regla de descarte |
|---|---|---|---|
| A1 | Tendencia 1D largo/caja frente a mantener BTC/ETH y efectivo | Universo disponible, costes, exposición máxima y presupuesto; regla de entrada/salida sencilla congelada | No mejora el objetivo ajustado por riesgo/costes o depende de una sola fase alcista |
| A2 | Tendencia 4h frente a A1 y a una referencia 4h simple | Mismos límites, datos y supuestos; contar la frecuencia adicional | La rotación y fricciones consumen la mejora o pequeñas variaciones destruyen el resultado |
| A3 | Selección por fuerza relativa frente a una cesta equiponderada | Universo histórico líquido, calendario y límites; comparar beta y concentración | La ventaja desaparece al igualar exposición, incluir bajas del universo y costes |
| A4 | Base congelada con un filtro de régimen sencillo frente a la misma base | Entrada, salida, tamaño y conjunto de oportunidades | No hay mejora posterior más allá de reducir exposición; sensibilidad excesiva al umbral |
| A5 | Ejecución maker, taker y maker con caducidad sobre los mismos candidatos | Señales y ventanas disponibles; medir decisiones sin fill | Mejora aparente debida a fills irreales o pérdidas por selección adversa/oportunidad |
| A6 | Valor incremental de IA y metamodelo, por módulos | Base, riesgo, tiempos y costes; registrar candidatos rechazados | Intervalo de mejora incluye deterioro material o no compensa coste/latencia |
| A7 | Deriva posterior a noticias verificadas frente a base sin noticias | Momento real de disponibilidad y retraso operable; nada de información futura | El beneficio está en la reacción ya ocurrida o solo aparece con precios imposibles de ejecutar |

Protocolo común: separar descubrimiento y selección; validación temporal móvil con purga de etiquetas solapadas y embargo justificado por horizonte/latencia; un tramo final realmente reservado, más observación futura desde el congelado. Ajustes, escalado de variables y selección de universo utilizan solo pasado. No mezclar aleatoriamente velas dependientes como si fueran ejemplos independientes.

Registrar PnL neto, exposición, rotación, drawdown, expectativa por operación y unidad de riesgo, distribución de pérdidas, duración, frecuencia, costes y estabilidad por activo/régimen. Calcular intervalos mediante remuestreo por bloques y escenarios conjuntos; no asumir independencia por contar muchas operaciones correlacionadas. El DSR y, si procede, PBO deben reflejar las búsquedas acumuladas, con sus supuestos y limitaciones.

Una vez consultado el tramo final deja de ser secreto. Un cambio inspirado por ese resultado se investiga con datos de desarrollo y necesita evaluación posterior nueva; renombrar el reporte no crea independencia.

## 6. Economía real y condiciones para promover

La métrica central es expectativa neta con incertidumbre, no acierto ni objetivos de take profit. Si W y L son medias netas positivas de ganadoras y pérdidas absolutas, respectivamente:

`E_neta = p × W − (1 − p) × L`

Las medias ya netas no deben volver a descontar comisiones. Si se parte de rendimientos brutos, se restan ambas comisiones, spread, deslizamiento, financiación y demás costes aplicables. El actual [economics.py](C:/Users/mario/Desktop/Trading/backend/app/economics.py) sí incluye perfiles de costes y los marca como no verificados; no se detectó un supuesto doble cobro en ese módulo.

La [tarifa pública de Binance consultada](https://www.binance.com/en/fee/trading) muestra 0,100% maker y 0,100% taker para usuario regular en su columna estándar. Las condiciones del par y la cuenta pueden diferir. Se debe consultar la tarifa efectiva y las comisiones de los fills: la [documentación oficial](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/commission_faq.md) describe consultas de comisión y órdenes de prueba. No se han solicitado credenciales ni hecho llamadas privadas en esta revisión.

Con esa tarifa estándar ilustrativa, una ida y vuelta supone aproximadamente 0,20% de nominal solo en comisiones, antes del resto de fricciones. Capturar movimientos pequeños exige superar esa barrera de manera consistente. Una orden limitada puede mejorar precio, pero no se debe asumir que siempre ahorra tarifa o se ejecuta.

Además hay gastos fijos: datos, servidores, modelos, suscripciones atribuibles y operación. Ejemplo puramente aritmético: 5 EUR al mes imputados a 100 EUR de capital requieren un 5% mensual solo para cubrir ese gasto fijo. No es un objetivo viable demostrado ni una estimación de la factura actual. Con capital pequeño, el objetivo inicial razonable del sistema es demostrar una ventaja y su capacidad, no sustituir un sueldo.

### Condiciones propuestas para promoción

Estos umbrales son una política de investigación inicial a ajustar antes de ver resultados, no constantes científicas ni garantía de beneficios:

1. **Integridad:** resolver P0; pasar pruebas de regresión de los fallos encontrados; reconstruir órdenes, reservas y caja tras reinicios, sin diferencias sin explicar.
2. **Equivalencia:** decisiones, cantidad y salidas coinciden entre replay y política congelada ante los mismos eventos; excepciones documentadas y medidas.
3. **Costes:** perfiles por cuenta/instrumento y estrés tanto de política completa como de las mismas operaciones y cantidades. El estrés actual puede filtrar entradas y reducir tamaño: un resultado positivo a doble coste no demuestra que los mismos trades resistan ese coste.
4. **Evidencia económica:** expectativa neta positiva con límite inferior de intervalo por bloques por encima de cero, estabilidad entre periodos/activos y corrección por selección. Como filtro orientativo, PF posterior >1,2 y PF >1 en estrés, además de drawdown y concentración aceptados previamente. No sustituir un control más estricto existente por estos números.
5. **Muestra:** planificar del orden de 100–200 cierres suficientemente independientes y varios regímenes; para baja frecuencia puede requerir un periodo largo, con referencia de 6–12 meses de observación futura. La precisión y dependencia reales mandan; ni un número de trades ni un plazo por sí solos aprueban la estrategia. No fabricar frecuencia para alcanzar el contador.
6. **Comparación:** mejorar el objetivo elegido frente a efectivo y referencias pasivas con riesgos comparables. Se puede aceptar menor retorno bruto si reduce suficientemente riesgo, pero se debe fijar antes el objetivo y comparar ambas dimensiones.
7. **IA:** cualquier ampliación de riesgo por un modelo exige prueba posterior de su mejora incremental. DSR ≥0,95 solo resulta interpretable con búsqueda y supuestos documentados; el valor aislado actual no concede aprobación.
8. **Operación:** límites de pérdida y exposición, antigüedad de datos, conciliación, supervisión independiente y procedimiento de parada ensayados. Un stop no garantiza el precio ni elimina saltos de mercado.

Si un candidato supera todo ello, el siguiente paso sería una prueba limitada, con presupuesto de pérdida previamente definido y decisión explícita del usuario. El aumento gradual dependería de ejecución y deterioro observado, no de haber llegado a una fecha del calendario. Esta revisión no autoriza ni activa ese paso.

El perfil actual permite 1,5% de riesgo por entrada de mesa y 6% agregado local, y el laboratorio admite un 50% del tamaño normal en fase de prueba: [aggressive.py:13](C:/Users/mario/Desktop/Trading/backend/app/aggressive.py:13). Existen límites globales adicionales de 3% de riesgo y 1,5% por factor sobre las cuentas incluidas. Son techos de diseño, no una recomendación de riesgo óptimo ni evidencia de rentabilidad. Antes de una eventual prueba real deben definirse sobre capital efectivamente asignado y tolerancia a pérdida; no trasladar automáticamente las capacidades virtuales.

## 7. Profesionalización de datos, ejecución y operación

| Área | Propuesta concreta | Criterio de aceptación |
|---|---|---|
| Datos | Históricos versionados, UTC, hora de disponibilidad, acciones corporativas, bajas del universo, contratos y origen | Una decisión puede reconstruirse con lo conocido entonces; huecos y revisiones quedan visibles |
| Instrumentos | Distinguir spot, CFD, futuro, acción y proxy; tarifas, multiplicadores, sesiones y financiación por contrato | Ningún proxy o precio indicativo valida ejecución de otro instrumento |
| Experimentos | Registro inmutable de hipótesis, hashes, código, costes, parámetros y todas las pruebas | Es posible contar búsquedas y demostrar cuándo se congeló cada política |
| Contabilidad | Libro de eventos de efectivo, fills, comisiones, financiación, conversiones y transferencias | Balance, equity y rendimiento se reconstruyen; las aportaciones no aumentan PnL |
| Ejecución | Máquina de estados persistente de órdenes, reservas e intentos, con conciliación | Fallos de red o reinicios no duplican órdenes ni borran riesgo incierto |
| Calidad de ejecución | Precio solicitado/ejecutado, latencia, parciales, rechazos, cancelaciones y precio posterior al fill | Se conocen costes reales y selección adversa; las modificaciones de stop tienen acuse comprobado |
| Riesgo | Presupuesto único por capital realmente asignado, monedas, factores y escenarios correlacionados | No se multiplica el capital por contar varias cuentas virtuales; límites se conservan al reiniciar |
| Panel | Política, fecha, muestra, costes verificados/supuestos, validación, intervalo, balance y equity | El usuario distingue saldo, rentabilidad, confianza y estado técnico; ninguna puntuación sin calibrar se llama probabilidad |
| Supervisión | Latido de progreso, edad de datos, cola de órdenes y conciliación, además de tarea viva | Un bucle atascado se detecta aunque su tarea no haya terminado; las salidas no dependen del LLM |
| Recuperación | Copia consistente de SQLite y ensayo de restauración de todos los libros | Recuperar estado y conciliar antes de reanudar; objetivo de recuperación fijado y medido |
| Despliegue | Dependencias reproducibles, CI, pruebas de regresión y cambios revisables | Una versión exacta puede reconstruirse; no se hace un cambio automático por una propuesta de agente |
| Acceso | Conservar API en loopback/túnel; autenticar y limitar acciones si se amplía acceso | Los endpoints de control no quedan expuestos sin autenticación; CORS no se considera autenticación |

Ya existen buenas decisiones: spot separado como paper; verificación de DEMO en el gateway; intents antes del envío; no reenvío ciego de respuestas inciertas; guardado conjunto de caja/posición; controles de costes; monitor de riesgo separado y contenedor con usuario sin privilegios, almacenamiento acotado y puerto publicado en loopback. Conviene conservarlas.

El healthcheck actual verifica que tareas existan y sigan vivas; ampliar a progreso y calidad. La protección local contra suspensión automática no cubre suspensión manual o apagado. El Compose actual limita publicación a `127.0.0.1`; la API no tiene autenticación de aplicación para sus controles: mantener ese perímetro y añadir control de acceso antes de ofrecerla a otros usuarios. No se ha afirmado que esté públicamente expuesta hoy.

Si se pretende gestionar dinero ajeno, asesorar o comercializar señales, revisar obligaciones y licencias de datos según el servicio concreto. La [guía de ESMA sobre IA en servicios de inversión](https://www.esma.europa.eu/press-news/esma-news/esma-provides-guidance-firms-using-artificial-intelligence-investment-services) es una referencia de gobernanza para ese escenario, no una afirmación de que este proyecto personal esté sujeto a todas sus obligaciones.

## 8. Tareas ya encargadas a los agentes

Se utilizó el coordinador **Grok Bot** y el grupo **Mesa Siete**, con los cuatro bots visibles de la aplicación. El encargo exige investigación, propuestas, fuentes primarias y revisión de solo lectura, manteniendo paper/DEMO, sin armar ni cambiar riesgo, código o cuentas.

| Agente | Encargo enviado | Entregable solicitado |
|---|---|---|
| Radar de Mercado | Comparar tendencia, fuerza relativa, reversión, noticias y límites de scalping/market making | Tabla de al menos ocho fuentes originales, mercado, horizonte, costes, evidencia fuera de muestra y limitaciones |
| Verificador de Noticias | Auditar fuentes, fechas, disponibilidad, costes, supervivencia y extrapolaciones de futuros/short a spot | Dictamen confirmado/no confirmado/descartado, con URL y discrepancias |
| Investigador Cuantitativo | Preparar 6–8 experimentos preregistrados y ablaciones de IA frente a reglas | Hipótesis, datos causales, referencias, validación temporal, costes y regla de rechazo |
| Revisor Técnico | Auditar el repositorio local de solo lectura, ejecución, riesgo y equivalencia | Diez riesgos principales con archivo/línea, reproducción y solución propuesta |
| Grok Bot, coordinador | Integrar los cuatro trabajos y resolver contradicciones en Mesa Siete | Prioridades P0/P1/P2 y plan 7/30/90 días con pruebas de aceptación |

La interfaz confirmó recepción y comunicaciones de los especialistas. El coordinador indicó que Radar entregó diez fuentes, Investigador siete experimentos y Verificador su dictamen; Revisor publicó un primer avance y estaba completando la tabla técnica. También se enviaron precisiones sobre las versiones vigentes y los fallos de pausa, cierre, reservas, FLOW y holdout. No se considera una afirmación del coordinador sustituto de la comprobación independiente: las conclusiones de este informe se sustentan en el código, los agregados y las fuentes revisadas aquí.

En la comprobación final de Mesa Siete se observaron los mensajes técnicos con archivo/línea sobre los contratos añadidos y la confirmación del coordinador de que quedaba cerrada el acta consolidada. No se han exportado los documentos completos de esos bots a este repositorio ni utilizado referencias que solo ellos afirman haber verificado.

Los tres agentes locales completaron, respectivamente, auditoría de validación, contratos de ejecución y evidencia contable. Ninguno modificó motores o configuración. La única escritura delegada fue el JSON agregado de evidencia.

## 9. Hoja de ruta con entregables verificables

| Fase | Trabajo | Condición de salida |
|---|---|---|
| Días 1–7 | Resolver P0 y discrepancias FLOW/cooldown; congelar una base; catálogo de versiones y costes; registrar decisiones futuras | Reproducciones fallidas antes y correctas después; cero riesgo incierto perdido; replay y política coincidentes |
| Días 8–30 | Datos causales, registro de experimentos, A1–A4 acotados; métricas de ejecución; iniciar comparación IA en sombra | Informes reproducibles con todas las variantes, costes, incertidumbre y resultados negativos incluidos |
| Días 31–90 | Acumular observación posterior sin cambiar continuamente la base; A5–A7 solo con datos adecuados; estrés y restauración | Decisión documentada: continuar, descartar o ampliar observación; no promoción por calendario |
| Después, si procede | Evaluación de una prueba limitada y escalado condicionado | Todas las puertas superadas y presupuesto de pérdida/operación decidido explícitamente |

Estos plazos son de trabajo y producción de evidencia. Una estrategia de baja frecuencia puede necesitar bastante más de 90 días. Si al final no hay ventaja, el resultado profesional es descartar o rediseñar la hipótesis, conservando los datos y sin incrementar riesgo para recuperar pérdidas.

Las primeras mejoras rápidas medibles son evitar entradas indebidas, recuperar cierres correctamente, conservar riesgo al reiniciar y dejar de gastar en variaciones sin evidencia. El beneficio económico de una estrategia seguirá siendo una hipótesis hasta superar una evaluación honesta y la ejecución correspondiente.

## 10. Registro de fuentes primarias consultadas

Consulta: 25/10/2026. Se contrastaron publicaciones originales, repositorios de autores, documentación del proveedor y autoridades. La antigüedad del trabajo empírico y el alcance de su muestra forman parte de su limitación; una fecha reciente de consulta no renueva sus resultados.

| Fuente | Fecha/versión relevante | Utilidad y límite para Mesa Siete |
|---|---|---|
| [Liu, Tsyvinski y Wu — Common Risk Factors in Cryptocurrency](https://www.nber.org/papers/w25882.pdf) | Documento de trabajo de 2019, publicado en Journal of Finance en 2022 | Factores de mercado, tamaño y momentum; no valida nuestros horizontes intradía ni spot solo largo |
| [Liu y Tsyvinski — Risks and Returns of Cryptocurrency](https://www.nber.org/papers/w24877) | 2018; publicación posterior en RFS, 2021 | Momentum y atención en una muestra histórica temprana; motivación, no señal de compra actual |
| [Hurst, Ooi y Pedersen — A Century of Evidence on Trend-Following Investing](https://www.aqr.com/insights/research/journal-article/a-century-of-evidence-on-trend-following-investing) | 31/10/2017 | Evidencia histórica multiactivo; mercados y estrategia distintos del motor cripto |
| [Moreira y Muir — Volatility Managed Portfolios](https://www.nber.org/papers/w22208) | 2016; Journal of Finance, 2017 | Hipótesis para controlar exposición por volatilidad; necesita límites y validación propia |
| [Zaremba et al. — Up or down? Short-term reversal, momentum, and liquidity effects in cryptocurrency markets](https://www.sciencedirect.com/science/article/pii/S1057521921002349) | International Review of Financial Analysis, noviembre de 2021 | La relación entre momentum y reversión depende de liquidez; no valida nuestros pares o ejecución |
| [Gu, Kelly y Xiu — Empirical Asset Pricing via Machine Learning](https://doi.org/10.1093/rfs/hhaa009) | RFS, 2020 | Referencia para modelos tabulares y comparación temporal; no evidencia de bots conversacionales |
| [Kelly y Xiu — Financial Machine Learning](https://www.nber.org/papers/w31502) | 2023 | Revisión metodológica; no es un experimento de este sistema |
| [Bailey et al. — Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) | Revisión febrero de 2015 | Selección entre ensayos y PBO; exige diseñar y justificar su aplicación |
| [Bailey y López de Prado — Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) | Julio de 2014 | Corrección por selección y no normalidad; no convierte una muestra pequeña en evidencia suficiente |
| [Lopez-Lira y Tang — Can ChatGPT Forecast Stock Price Movements?](https://arxiv.org/abs/2304.07619) | v6, 28/10/2025 | Distingue reacción no operable y deriva; posible erosión de la ventaja |
| [Glasserman y Lin — Assessing Look-Ahead Bias…](https://arxiv.org/abs/2309.17322) | 29/09/2023 | Contaminación y conocimiento previo en pruebas retrospectivas de sentimiento |
| [Avellaneda y Stoikov — High-frequency trading in a limit order book](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf) | Quantitative Finance, 2008 | Modelo de inventario y ejecución; no reproduce nuestra cola ni garantiza fills |
| [BIS — Crypto carry](https://www.bis.org/publ/work1087.pdf) | Abril de 2023; revisión octubre de 2025 | Base spot/futuros y riesgos de margen, liquidación y segmentación |
| [Dhawan y Putniņš — A New Wolf in Town?](https://revfin.org/a-new-wolf-in-town-pump-and-dump-manipulation-in-cryptocurrency-markets/) | Review of Finance, 2023 | Manipulación de cripto; fundamento para filtros de liquidez y cautela con atención social |
| [Binance — tabla de tarifas](https://www.binance.com/en/fee/trading) | Página vigente consultada hoy | Referencia pública; la tarifa efectiva se verifica por par/cuenta |
| [Binance — Commission FAQ](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/commission_faq.md) | Documentación viva consultada hoy | Tasas estándar/especiales/impuestos/descuentos y consulta; ejemplos del documento no son tarifas reales |
| [ESMA — Warning on the use of AI for investing](https://www.esma.europa.eu/document/warning-use-ai-investing) | 2025 | Límites del uso de herramientas de IA para decisiones de inversión |
| [ESMA — AI in investment services](https://www.esma.europa.eu/press-news/esma-news/esma-provides-guidance-firms-using-artificial-intelligence-investment-services) | 30/05/2024 | Referencia si se transforma en servicio de inversión para terceros |

La búsqueda es amplia y orientada a decisiones del proyecto; no es una revisión sistemática de toda la literatura ni permite ordenar universalmente estrategias por beneficio futuro. No se han usado rankings promocionales de bots, rentabilidades anunciadas o capturas de ganancias como evidencia de selección.
