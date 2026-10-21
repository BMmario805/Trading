# Corrección del diagnóstico y cambios aplicados

21 de octubre de 2026 · Europe/Madrid.

El adjunto contenía documentación histórica, código y una propuesta de cambios.
Se han implementado los cambios locales en simulación y DEMO, conservando libros,
operaciones y saldos. Los resultados exploratorios no validan una estrategia ni
permiten afirmar que el sistema será rentable después de estos cambios.

## Mesa Cripto

- Nuevas entradas en **1 h**, plazo máximo **48 h**, con una política identificada
  como `desk-partial-1h-v4`. Cada operación conserva su marco, plazo, costes y
  política de salida. Las posiciones anteriores siguen gestionándose con sus
  reglas originales; Forex y Acciones mantienen 15 minutos y seis horas.
- Al alcanzar **1,5 R netos por unidad**, vende aproximadamente el **50 %**,
  redondeado al paso del mercado. Tanto la venta como el resto deben cumplir
  lote mínimo, máximo y nominal. Si no pueden dividirse, conserva la salida
  completa al objetivo de referencia, sin generar un resto no negociable.
- Después del parcial, el stop cubre entrada, comisión y salida con
  deslizamiento. El resto sigue un trailing de **2,5 ATR**, sin retroceder el
  stop. El ATR de gestión es el fijado al entrar. El objetivo de 3 ATR pasa a
  ser una referencia para el filtro de costes, no una liquidación automática
  del resto. Este filtro geométrico no estima la expectativa de la nueva salida.
- Cada parcial registra cantidad, precio, fecha, comisión, ingreso y resultado.
  El cierre final consolida una sola operación con todos sus costes. Efectivo
  y operación se guardan en la misma transacción; un fallo revierte el cambio.
  Reiniciar o repetir una cotización no vuelve a vender el mismo parcial.
- Se usa la cotización observada y fresca, con sus gaps. No se supone una
  ejecución perfecta exactamente en 1,5 R. Pausar mantiene la gestión de stops
  y parciales; un cierre solicitado tiene prioridad sobre tomar beneficios.

La comprobación de ejecución detectó además un catálogo incompleto de acciones
y ETF tokenizados. Se completó la exclusión con los instrumentos confirmados
en los anuncios oficiales de Binance del 28 de agosto y 21 de octubre,
incluidos KORUB, SNXXB y ETF 2x/3x. Los instrumentos identificados fuera del
universo se cierran únicamente con precio fresco y conservan su operación e
historial de costes. No se aplica una regla genérica por sufijo que excluya
criptomonedas válidas como BNB. El catálogo está fechado y requiere actualizarse
cuando el proveedor añada instrumentos nuevos; `exchangeInfo` no los distingue
por un campo público de clase de activo en la respuesta examinada.
[Catálogo bStocks](https://www.binance.com/en/support/announcement/detail/4fea39f8b6e54e1b88e388fc90ce323e),
[Ampliación del 21 de octubre](https://www.binance.com/en/support/announcement/detail/b965e028041c4d0fbb4291372f55e4d4).

El barrido revisa **todo el universo elegible** antes de asignar capital. Las
descargas se agrupan de 40 en 40, con un máximo de cuatro simultáneas y caché
por vela cerrada. Los controles de riesgo mantienen su tarea independiente.

Se calculan señal, tamaño y geometría de costes para cada candidato disponible.
El selector recibe hasta 24 setups, ordenados localmente entre todos los aptos,
con una cota de 11.500 bytes. La cobertura evaluada y enviada queda visible en
la API; no se afirma que una petición contenga todos los mercados del exchange.

Gemini puede elegir un único mercado o efectivo. Una respuesta válida limita
las entradas durante esa vela y persiste tras reiniciar: los demás candidatos
no se compran simplemente por quedar huecos. Sin respuesta utilizable se usa
una prioridad local entre los setups viables, sin atribuirle confianza de IA.
Se mantienen los límites globales y el presupuesto ya configurados de Gemini.

El contexto incluye ATR, extensión, volumen, spread, ratio neto de referencia y
correlación positiva máxima de retornos con las posiciones abiertas. Solo usa
velas cerradas coincidentes y al menos veinte retornos. Si falta histórico,
envía `null`, no una correlación inventada; no envía saldos ni posiciones al
proveedor. La correlación observada no garantiza diversificación futura.

## Laboratorio y aprendizaje

El modo a prueba pasa de cinco a **una posición por bot**, con un máximo del
**15 % del riesgo habitual**. Los vetos y los controles agregados siguen
aplicándose. Las posiciones antiguas se recuperan y gestionan; no se cierran
solo para ajustar el nuevo cupo. Se ha elegido esta alternativa de la propuesta,
sin crear un libro shadow ni mezclar operaciones ficticias con el historial.

El meta-modelo pondera cada cierre mediante
`min(3, max(0.25, abs(pnl_neto / riesgo_inicial)))`. El riesgo usa su distancia
económica original y la conversión a la moneda de la cuenta. No lo reconstruye
desde un stop movido. Si falta ese riesgo, el cierre no entrena el modelo.

La salida sigue siendo una **puntuación logística ponderada por R**, no una
probabilidad calibrada ni una predicción directa de expectativa R. Las etiquetas
y la API indican esta diferencia. La ponderación está acotada y no sustituye
una evaluación monetaria; tampoco autoriza aumentar el riesgo.

Trend usa tres bloques: **40 % régimen ADX/EMA200/marco superior cerrado,
40 % ruptura Donchian 40/55 y 20 % CMF**. Sin ruptura o sin régimen coherente
no hay entrada. El detalle de confirmaciones muestra esos mismos tres bloques.
Trend y Squeeze pasan a **1 h / 4 h**; Swing mantiene **1 h / 1 día**.

Scalper exige un ATR de al menos diez veces el spread, equivalente a spread
máximo del 10 % del ATR. El filtro está compartido por ejecución y simulación,
incluida la DEMO MT5. Los demás bots conservan el mínimo de tres veces.

El motor se identifica como **versión 5** y el replay MT5 como versión 2. Los
parámetros de versiones anteriores siguen en el historial, pero necesitan
revalidarse para autorizar tamaño completo. El meta-modelo comienza una nueva
generación compatible. Los beneficios exploratorios de SOL/4 h no se promocionan
automáticamente ni se declaran validados.

## Catalizadores de Gemini

Se añade una regla local de veto antes de abrir en las mesas, el Laboratorio y
MT5 DEMO. Usa únicamente una clasificación ya guardada del **activo exacto**:

- Análisis y publicación originales con menos de dos horas, sin fecha futura.
- Texto relevante y sin marca de ruido.
- Catalizador `security`, `regulation` o `monetary_policy`.
- Dirección contraria a la operación y confianza de clasificación ≥ 0,75.

La regla solo puede impedir una entrada. No aumenta tamaño, cambia stops ni
hace llamadas adicionales al modelo. Una noticia de BTC no se atribuye a SOL;
sin observación aplicable continúan los controles técnicos. La clasificación
puede equivocarse y este veto aún no ha demostrado mejorar resultados. Los
mercados observados siguen siendo los configurados, no todos los pares del
exchange. No se han contratado Jev, Grok u otras fuentes.

## MT5: capital virtual y lotes

La sesión DEMO en EUR recibe una **aportación virtual hasta 1.000 EUR**, sin
cambiar el saldo del bróker, la fecha de corte ni el historial de operaciones.
La aportación es idempotente y se registra separada del beneficio; las curvas
de PnL y la reconciliación no la cuentan como rentabilidad. El ajuste diario
conserva la pérdida monetaria anterior y no levanta una pausa ya aplicada.

`virtual_budget_eur` en la configuración local permite fijar entre 1.000 y
2.500 EUR. No reduce una asignación existente. El porcentaje base de riesgo
se conserva: sigue prohibido forzar el lote mínimo por encima del presupuesto.
La API mantiene el diagnóstico de riesgo del lote mínimo y capital requerido.
Mil euros tampoco garantizan que todos los contratos sean negociables.
Las sesiones en otras divisas se conservan sin convertirlas ficticiamente.

## Maker y continuidad

Se añade `app.maker_simulation`, un simulador post-only separado del libro
principal. Recibe un libro y operaciones posteriores observadas en JSONL.
No da por ejecutada una orden al tocar el bid/ask: descuenta la cola publicada,
requiere volumen agresor compatible, limita el fill al volumen observado,
descarta duplicados y eventos antiguos, y cancela ante cortes de datos. Expone
fills parciales, caducidad y comisiones explícitas. El modelo de cola no conoce
cancelaciones ni la prioridad real y no acredita una ejecución real.

Ejemplo desde `backend/`:

```text
.venv/Scripts/python.exe -m app.maker_simulation --input observaciones.jsonl --symbol BTCUSDT --side buy --quantity 0.001 --fee-rate 0.001 --ttl 60
```

El primer registro contiene `symbol`, `at`, `bidPrice`, `askPrice`, `bidQty` y
`askQty`. Los siguientes contienen `symbol`, `id`, `at`, `price`, `quantity` e
`isBuyerMaker`; las fechas se expresan en segundos. Un registro de tipo
`disconnect` cancela lo pendiente. No se ha añadido una pasarela Binance real
ni sustituido automáticamente las entradas de la mesa por fills maker supuestos.

El ahorro del **50 %** y el umbral ATR del **0,18 %** del adjunto no están
demostrados. Binance distingue comisión maker, taker y descuentos por cuenta y
símbolo; una orden post-only no garantiza un descuento concreto, ejecución ni
ausencia de selección adversa. [Comisiones oficiales de Binance](https://developers.binance.com/en/docs/products/spot/faqs/commission_faq).

`deploy/retry_oracle.py` prepara reintentos de Madrid alternando A1 ARM de
1 OCPU/6 GB y E2.1.Micro AMD64. Exige una imagen compatible para cada shape y
una subred existente. Por defecto solo valida e imprime el plan. Con `--apply`
usa el SDK oficial OCI, un diario SQLite y un token de idempotencia por intento.
No crea otra instancia si ya existe, espera quince minutos tras falta de
capacidad confirmada y se detiene ante respuestas inciertas.

No se ha ejecutado contra Oracle, instalado un planificador ni contratado un
VPS. Faltan identificadores de compartimento, subred e imágenes y credenciales
OCI configuradas; el fichero local anterior no los contiene. Tampoco se ha
confirmado disponibilidad actual ni gratuidad de una nueva instancia. La
migración definitiva debe conservar una única copia del motor activa.
[Creación y capacidad en OCI](https://docs.oracle.com/en-us/iaas/Content/Compute/known-issues.htm),
[SDK y token de reintento](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/core/client/oci.core.ComputeClient.html).

La protección local contra inactividad ya existía. No elimina suspensión
explícita, apagados ni caídas de red. La coincidencia entre suspensiones y
pérdidas grandes no demuestra cuánto habría evitado un servidor remoto sin
los ticks que faltan. Los stops del libro simulado dependen de que su motor
esté disponible; los stops enviados a MT5 pertenecen al bróker.

## Errores numéricos y conclusiones del adjunto

El informe original de salidas prueba 72 escenarios sobre nueve datasets
solapados con investigación anterior. **No transforma los nueve casos en
beneficios**: BTC/1 h, ETH/15 min, ETH/1 h y SOL/15 min pierden con ambas salidas
parciales. BTC/4 h gana con 48 h, pero pierde sin límite temporal; no son
políticas intercambiables. En BTC/15 min, los cuatro escenarios con costes
dobles tienen **cero operaciones**: su 0,00 no es evidencia de resistencia.

Estos resultados son exploratorios y no prueban causalmente que las pérdidas
procedan solo de las salidas o la fricción. El diagnóstico anterior ya mostraba
resultado negativo antes de comisiones y deslizamiento reconstruidos. La cifra
de 31 cierres es histórica, no un contador actualizado. Se conservan los
informes originales y sus resultados íntegros para auditoría.

No se sostienen las afirmaciones de «multiplicar por diez la utilidad» del
agente, evitar necesariamente hackeos con sentimiento, duplicar configuraciones
rentables o recuperar las pérdidas al cambiar de plazo. Los cambios necesitan
evaluación posterior con costes, incertidumbre y periodos futuros.

## Verificación

Pruebas automáticas de contabilidad, reinicio, duplicados, lotes, precios
antiguos, selección exclusiva, efectivo, cobertura del universo, causalidad,
ponderación en R, veto contextual, aportaciones virtuales, fills y reintentos
OCI simulados. Compilación del panel y análisis estático. El arranque local se
comprueba con cuatro escenarios de procesos simulados antes de recargar.

Resultado: **330 pruebas Python correctas**, cuatro escenarios de arranque
PowerShell correctos y panel compilado. El análisis estático no presenta
errores; conserva tres advertencias anteriores en Watchlist y BotPerformance.

El servicio local se ha recargado y `/api/health` confirma tres mesas y siete
bots en modo paper. En la primera lectura tras recargar se recuperaron las tres
posiciones anteriores de Cripto con cantidades, stops y plazo de seis horas,
y las dieciocho del Laboratorio con sus balances. Durante la vigilancia posterior
ICP y SNXXB cerraron por sus stops anteriores y NEAR de Squeeze por stop-loss;
los movimientos se registraron en los libros y reconciliaron el efectivo.
La versión final excluye los bStocks omitidos; el cierre simulado de KORUB
queda identificado como salida por instrumento fuera del universo. Las nuevas
entradas elegibles publican sus reglas de 1 h y salida parcial.
MT5 confirma una única aportación virtual de 900 EUR, capital asignado de
1.000 EUR, PnL cero por esa aportación y diferencia de reconciliación cero.
El panel local responde correctamente. Una fuente de cotización NVDA del
Laboratorio figuró temporalmente no disponible: el monitor mantiene ese estado
visible y no se simula un precio de cierre para esa posición.

La copia previa, con integridad SQLite comprobada y sin claves, está en
`.runtime/corrections-before-1792570015903922100/`. El resultado de pruebas y la
lectura posterior están en `.runtime/corrections-tests.log` y
`.runtime/corrections-verification.json`. Los ensayos de maker y OCI usan datos
y respuestas simuladas; no prueban fills reales ni creación de infraestructura.
