# Actividad del Laboratorio y MT5

21 de octubre de 2026 · Europe/Madrid.

La captura corresponde a **MT5 DEMO**, que no había registrado operaciones
en su sesión de seguimiento. El Laboratorio es independiente y, en la lectura
comprobada, sus siete bots estaban activos, con **16 posiciones abiertas y 56
cierres**: Swing 5 abiertas, Tendencia 5, Squeeze 3 y Flujo 3. Scalper, Reversión
y Apertura estaban sin posición, con sus motivos de espera disponibles.

## Fallo corregido

Las cotizaciones activas del terminal mostraban el desfase horario ya
confirmado de tres horas, más un adelanto residual de unos 6–7 segundos. El
filtro anterior rechazaba cualquier adelanto superior a cinco segundos. Por
eso algunos mercados líquidos aparecían como cotización no reciente aunque
los ticks avanzaban.

Se admite un adelanto acotado de **15 segundos** en la calibración y validación
del tick. El desfase sigue requiriendo ticks que avancen; no se recalibra usando
un precio congelado. El máximo de antigüedad permanece en **120 segundos**.
Las velas abiertas siguen excluidas y no se desplaza su cierre para fabricar
señales. Se mantienen las protecciones de cuenta DEMO y riesgo monetario.

Este comportamiento se apoya en la observación concreta del terminal, no en
dar por supuesto que todos los brókeres usan un huso horario distinto. La
[documentación de MT5](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrom_py)
describe las marcas temporales de la integración en UTC.

## Por qué MT5 sigue sin entrar en la lectura verificada

Tras recargar se revisaron 45 combinaciones sin rechazos falsos por el reloj:

| Motivo | Combinaciones |
|---|---:|
| Mercado cerrado o histórico sin actualizar | 26 |
| Sin señal nueva de entrada | 14 |
| Lote mínimo superior al presupuesto de riesgo | 4 |
| Volatilidad insuficiente para el diferencial | 1 |

La sesión tiene 1.000 EUR virtuales. El riesgo base del 0,1 % equivale a 1 EUR,
pero los pares sin validar usan el 15 % de ese riesgo: **0,15 EUR por entrada**.
En esa revisión el lote mínimo de GBPUSD para Scalper arriesgaba 0,27445 EUR;
el de XAUUSD para Swing, 31,20425 EUR. Esos rechazos son controles reales,
no un fallo de conexión. El saldo del bróker no sustituye el presupuesto
virtual autorizado. No se han aumentado capital, riesgo ni cupos para forzar
órdenes, ni se ha insertado una operación de prueba en el historial.

Las cifras describen esa revisión; las señales y riesgos mínimos varían con
las cotizaciones y el ATR. No se promete una operación en cada revisión.

## Panel corregido

- El Laboratorio aparece abierto antes de MT5, con recuento de bots activos,
  posiciones y cierres. Cada bot muestra los motivos principales de espera.
- MT5 muestra hora de la última revisión, combinaciones revisadas, órdenes
  aceptadas y causas de bloqueo. El lote mínimo y el riesgo permitido se
  comparan en una tabla visible.
- Los diagnósticos monetarios se renuevan en cada revisión: una señal que ya
  no existe no conserva un cálculo de riesgo antiguo como motivo actual.
- Se aclara que los ceros de MT5 no son los resultados del Laboratorio. Gemini
  se describe como clasificador que puede activar un veto, sin enviar órdenes.
- El historial MT5 se carga por separado para mantener el paquete principal
  por debajo de la advertencia de tamaño de compilación.

## Verificación

**335 pruebas Python correctas**, incluidas tolerancia al pequeño adelanto,
rechazo de ticks antiguos o demasiado futuros, imposibilidad de calibrar con
ticks congelados y resumen de bloqueos sin diagnósticos monetarios obsoletos.
El panel compila; el análisis estático conserva tres advertencias anteriores.
Servicio local recargado y estado confirmado mediante API y navegador.

Lecturas previas y posteriores: `.runtime/inactivity-before.json` y
`.runtime/inactivity-after.json`. Pruebas: `.runtime/inactivity-tests.log`.
