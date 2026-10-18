# Rendimiento: situación actual, mejoras y prioridades

18 de octubre de 2026 · Europe/Madrid. Importes de simulación; MT5 usa DEMO.

## Conclusión operativa

El sistema funciona como un laboratorio de estrategias con controles de riesgo.
**Todavía no dispone de una estrategia con ventaja económica validada.** El
principal avance para aumentar el beneficio esperado es obtener señales con
expectativa neta positiva y mantener una vigilancia continua. El apalancamiento,
la frecuencia y el capital multiplican el resultado de esa expectativa, incluido
su signo negativo; no la convierten en una ventaja.

Las mesas nuevas siguen activadas. Cripto conserva 104,57 USDT frente a 112,0953
USDT iniciales. Los cierres de la política nueva siguen siendo cero: sus cambios
no se evalúan usando los resultados de las reglas anteriores.

## Qué muestran las operaciones

Mesa cripto, 31 cierres históricos:

| Métrica | Resultado |
|---|---:|
| PnL neto realizado | −7,5221 USDT |
| Ganadores / perdedores | 7 / 24 |
| Acierto | 22,58 % |
| Ganancia media | 0,7027 USDT |
| Pérdida media | 0,5184 USDT |
| Expectativa descriptiva por cierre | −0,2426 USDT |
| Factor de beneficio neto | 0,3954 |
| Acierto de equilibrio usando las medias observadas | 42,45 % |
| Comisiones de esos cierres | 1,7427 USDT |
| Deslizamiento simulado reconstruido | 0,3485 USDT |
| Resultado antes de esos costes explícitos | −5,4308 USDT |

Eliminar esas dos fricciones no volvería positiva esta muestra. El spread sigue
incluido en las referencias; no existe un libro histórico para eliminarlo con
precisión. El acierto de equilibrio es una descripción de las medias de estos
cierres, no una previsión de las siguientes operaciones.

En el Laboratorio se mezclaban en las estadísticas cierres antiguos sin costes
completos y cierres nuevos netos. El nuevo diagnóstico los separa. Por ejemplo,
Squeeze mostraba cinco cierres y 60 % de acierto, pero los tres con costes completos
suman −4,3912 EUR: una ganancia de 0,5968 EUR y dos pérdidas que promedian 2,4940
EUR. RSI2 solo tiene dos cierres netos positivos, insuficientes para validarlo.

## Hallazgo de continuidad: suspensiones del equipo

El registro de energía de Windows contiene:

- Suspensión el **17 de octubre a las 11:25**, reanudación el **18 a las 00:35**.
- Suspensión el **18 de octubre a la 01:49**, reanudación a las **20:44**.
- Las entradas registradas indican motivo **Application API**. Este dato no
  identifica qué aplicación solicitó cada suspensión.

NEAR se abrió el 17 a las 11:00 y AVAX a las 11:15. Ambas posiciones de Squeeze
cerraron el 18 a las 00:38, poco después de la reanudación. Sus pérdidas de 2,6670
y 2,3210 EUR equivalen a unas **5,1R y 4,4R** frente a riesgos iniciales próximos
a 0,525 EUR. La coincidencia temporal es evidencia de una interrupción de la
vigilancia; sin ticks continuos no se puede separar cuánto correspondió a saltos
de precio, suspensión y demora de recuperación.

Un stop simulado ejecutado por este PC no actúa cuando el PC está suspendido.
Se añadió una solicitud temporal de actividad de Windows mientras vive el
servicio. Mantiene el equipo activo frente al temporizador de inactividad y
restaura su estado anterior al cerrar el proceso. La pantalla conserva su
temporizador. **La suspensión manual, las solicitudes explícitas de otras
aplicaciones, los apagados y las caídas de red siguen siendo limitaciones.**
No se han cambiado permanentemente los planes de energía ni se ha intentado
bloquear el cierre de tapa o el botón de encendido.

Para operar con capital real de forma continua, hacen falta stops nativos en el
exchange/bróker y una máquina estable siempre disponible. La protección local
añadida no sustituye esas dos medidas. [Comportamiento documentado por Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate).

## Mejoras aplicadas

1. **Vigilancia independiente en los siete bots.** Buscar entradas y descargar
   históricos ya no retrasa el siguiente ciclo de stops. Las cotizaciones de las
   posiciones se solicitan con concurrencia limitada y tiempos máximos. Se
   vuelven a comprobar los límites y la pausa antes de abrir tras una espera.
   Se conservan precios reales de salida ante gaps y cotizaciones antiguas
   bloqueadas: no se reescriben los resultados para cerrar mágicamente al stop.
2. **Una descarga de velas por cierre.** Se reutiliza el histórico de 15 minutos
   hasta que cierra otra vela. Un feed atrasado no queda congelado en caché;
   las cotizaciones de ejecución se siguen renovando. En condiciones normales,
   un libro para el lote sustituye diez peticiones individuales de libro; si el
   lote falla, se consulta cada mercado para aislar el problema.
3. **Diagnóstico económico en el panel.** Muestra expectativa neta, factor de
   beneficio, medias de ganancias y pérdidas, comisiones, financiación y cierres
   que superaron 1,5 veces su riesgo inicial. Distingue las políticas y separa
   los cierres antiguos sin costes completos. Conserva cada divisa por cuenta.
4. **Explicación de la inactividad.** Cuenta mercados revisados, señales que
   pasan, viabilidad por costes y tamaño. En una lectura, 77 mercados habían
   sido evaluados: 57 superaban costes/tamaño y ninguno la señal. Los 20
   bloqueados por costes también podían fallar por señal; no se suman ambos
   motivos como categorías excluyentes.
5. **Investigación más amplia.** El comparador del panel ahora prueba las mismas
   doce reglas en BTC, ETH y SOL, en 1 h y 4 h, con un año de datos. El modo de
   investigación admite hasta tres años, manteniendo el máximo de 10.000 velas
   y sin aplicar automáticamente parámetros al trading.
6. **Carga del panel.** El diagnóstico económico se carga como módulo separado;
   la compilación principal queda por debajo de la advertencia de 500 kB.

Las mejoras de continuidad y velocidad reducen defectos operativos; por sí solas
no acreditan que la señal genere beneficios.

## Costes y horizonte

Con la hipótesis cripto de comisión del 0,1 % y deslizamiento del 0,02 % por lado,
la fricción de ida y vuelta es aproximadamente 0,24 % antes del spread; la mediana
observada con spread rondaba 0,26–0,28 %. Con SL 1,5 ATR, TP 3 ATR y beneficio/riesgo
neto mínimo 1,2, el ATR necesita superar aproximadamente **0,404 % del precio**.
Esta frontera es matemática para esa geometría, no una garantía de éxito.

Las comisiones de una futura cuenta deben consultarse para ese usuario y símbolo,
incluyendo descuentos y tipo de orden. Una orden limitada puede reducir algunos
costes, pero introduce ausencia de ejecución y selección adversa; bajar la comisión
en el simulador sin medir fills produciría una mejora ficticia.
[Documentación oficial de comisiones](https://www.binance.com/en/academy/articles/how-to-calculate-transaction-fees-on-binance).

## Investigación realizada

Se conservaron todos los resultados de doce reglas fijas por dataset, tres bloques
cronológicos, embargo, un tramo final reservado y costes dobles. Los importes del
comparador usan una referencia de **100 USDT por instrumento, riesgo fijo de 1
USDT y nominal máximo de 100 USDT**, sin capitalización. No equivalen a la mesa
viva, que arriesga como máximo 0,5 % y asigna hasta 1/6 por entrada.

### Un año, 1 h y 4 h

Los seis datasets no produjeron ninguna candidata que superase todos los filtros.
En 4 h había reglas de rango positivas para BTC y ETH con costes dobles, pero solo
cinco a siete operaciones en descubrimiento. ETH/1 h tenía una variante Donchian
con +2,25 USDT, que pasaba a −1,24 USDT con costes dobles. Esos resultados no
justifican promocionar la estrategia.

[Informe de un año](profit-horizons-2026-10-18.json).

### Ampliación a tres años, 4 h

Se amplió la historia para investigar la escasez de muestra, manteniendo las doce
reglas. Se usaron 6.569 velas por instrumento. La ampliación solapa el informe
anterior y es exploratoria; no constituye confirmación independiente.

| Instrumento | Selección congelada antes del tramo final | Cierres finales | PnL final | Costes dobles | Promoción |
|---|---|---:|---:|---:|---|
| BTC | Donchian 60 | 10 | −3,58 USDT | −5,06 USDT | Rechazada |
| ETH | Momento con ajuste de volatilidad, 20 | 29 | −4,12 USDT | −5,97 USDT | Rechazada |
| SOL | Donchian 40 | 8 | +3,00 USDT | +2,12 USDT | Rechazada |

SOL/4 h es una hipótesis concreta para seguir investigando: su factor de beneficio
descriptivo es 2,0, pero solo hay ocho cierres, el intervalo de expectativa incluye
valores negativos y las variantes vecinas pierden. No se ha activado ni se ha
aumentado riesgo basándose en ese resultado. Escogerla entre todos los ensayos y
seguir retocándola con los mismos datos aumentaría el sesgo de selección.
[Informe de tres años](profit-swing-3y-2026-10-18.json),
[investigación sobre selección de backtests](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).

## Cómo potenciarlo a partir de aquí

| Prioridad | Trabajo concreto | Criterio de avance |
|---|---|---|
| 1. Continuidad | Investigar las suspensiones Application API; mantener una máquina siempre disponible y diseñar stops nativos para el adaptador real | Monitor estable, recuperación documentada y ninguna posición real dependiente exclusivamente del PC |
| 2. Ejecución | Medir comisiones de cuenta, bid/ask, deslizamiento, fills de órdenes limitadas y financiación; forex/acciones con feed de bróker | Replay coherente con contratos y ejecución observada |
| 3. Señal | Congelar una hipótesis como SOL/4 h y observarla en una cuenta simulada separada; registrar también rechazos y costes | Expectativa neta positiva fuera de muestra y en un periodo futuro; muestra suficiente y estabilidad entre variantes |
| 4. Asignación | Dar presupuesto a reglas que mantengan ventaja; usar el efectivo como alternativa; medir correlación entre monedas | Riesgo agregado y concentración controlados, comparando con efectivo y referencia pasiva de exposición comparable |
| 5. Escala | Aumentar gradualmente una regla ya verificada, con límites de pérdidas y retirada automática si se deteriora | Cada incremento respaldado por resultados netos posteriores, no por el mejor backtest |

Como criterio operativo propuesto, conviene buscar **50–100 cierres netos futuros**
por configuración y distintos regímenes, además de una expectativa con incertidumbre
compatible con una ventaja. No existe un número de operaciones que por sí solo
garantice rentabilidad. Las reglas lentas pueden requerir meses; forzar operaciones
para llegar antes al número destruye el propósito de la prueba.

Una cuenta de 100 EUR gana 1 EUR con un retorno del 1 %. Obtener beneficios absolutos
mucho mayores exige más capital o más retorno y riesgo; primero debe acreditarse
la ventaja. La IA puede apoyar clasificación e investigación, pero su confianza
textual no es una probabilidad calibrada de beneficio.

## Verificación

Pasan **276 pruebas automáticas**. Incluyen stop mientras el escaneo queda esperando,
vigilancia con entradas pausadas, cancelación de tareas, caché que cambia al cerrar
vela, feeds atrasados, libros por lote con alternativa individual, métricas netas
separadas de legacy, investigación Binance directa en 4 h y solicitud temporal de
actividad con restauración y fallo visible. El panel compila; el análisis estático
mantiene tres advertencias preexistentes.

Se comprobó en la API que la protección Windows está activa y que los siete bots
publican su monitor independiente. Los informes, instantáneas y seguimiento local
se conservan en `.runtime/profit-review-20261018/`. MT5 sigue en DEMO y las tres
Mesas Siete en simulación; no se ha movido dinero real.

La vigilancia posterior duró cuatro minutos, con 12 muestras: protección activa
en todas, ningún error de monitor en la última lectura y 39 reutilizaciones de
velas frente a 81 descargas, además de 12 consultas agrupadas de libro. Al final
había 83 mercados aptos y 76 con comprobaciones de señal y costes disponibles:
56 superaban costes/tamaño y ninguno la señal. La mesa cripto seguía sin nuevas
entradas. Las consultas de riesgo de los bots con posiciones tardaban alrededor
de 0,24–0,50 segundos en esa lectura; no es un compromiso de latencia futura.

Las Mesas Siete actuales compran con efectivo. En un mercado descendente pueden
permanecer planas por diseño. Los cortos del Laboratorio son una simulación de
margen con financiación supuesta, no una capacidad verificada de vender en corto
esos activos Spot. Abrir más posiciones requiere una hipótesis rentable y una
modalidad ejecutable, además de actividad visible.
