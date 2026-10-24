# Mesa Siete: suscripciones, pruebas y revisión cuantitativa

Fecha: 24 de octubre de 2026. Cuenta paper/demo; enjambre en sombra y sin armar.

## Conexión realizada

El transporte activo es `subscription`: Grok Build usa su sesión oficial y
ChatGPT participa mediante Codex autenticado con ChatGPT. No necesita claves
de API ni convierte una clave de Cursor en credenciales de inferencia. No hay
respaldo automático a APIs de pago. Se eliminaron las variables de claves del
entorno de los procesos hijos. Se consume cuota de los planes y no se habilitó
ninguna recarga. No se puede prometer disponibilidad al agotar esa cuota.

Se comprobó una respuesta estructurada independiente de cada cliente y un
ciclo completo en el servicio: pulso, flujo, auditor cuantitativo y commander
respondieron. El ciclo SPY terminó correctamente y vetó la entrada por flujo
no válido y ausencia de un trade económicamente evaluable. No abrió órdenes.

Evidencia técnica del ciclo: `.runtime/subscription-live-proof.json`.
Transporte: `backend/app/subscription_bridge.py`.
La configuración evita terminal/edición en las lecturas y solo autoriza
búsquedas públicas para el pulso. Solo se publica la respuesta final JSON;
no se publican campos de razonamiento interno de los clientes.

## Historial existente: no hay rentabilidad demostrada

La fotografía guardada ANTES de este cambio arroja:

- Cripto, todas las políticas: 47 cierres, PnL neto
  -21.1493 USDT; acierto 21.28 %;
  profit factor 0.203; expectativa por cierre
  -0.4500 USDT.
- PnL antes de costes explícitos reconstruidos:
  -18.3229 USDT. Las pérdidas no se explican
  únicamente por las comisiones.
- Política actual de cripto: 3 cierres, PnL
  -4.7312 USDT. Muestra insuficiente para validarla.

Son resultados del historial previo, no resultados atribuibles a los nuevos
agentes. No se mezclan con operaciones futuras ni con la cuenta MT5.

## Prueba histórica nueva

Se descargó un año de velas cerradas de Binance para BTC, LINK, ETH y SOL,
en 1 h y 4 h. Se compararon 12 variantes previamente definidas por dataset
(96 combinaciones), con tres bloques de descubrimiento, embargo, selección
congelada y tramo final reservado. Se repitieron costes al doble. No se
cambiaron umbrales después de ver los resultados ni se promovió una estrategia.

| Símbolo | Velas | Barras | Selección antes del tramo final | Cierres finales | Neto USDT | Doble coste USDT | Todos los controles |
|---|---|---:|---|---:|---:|---:|---|
| BTC | 1h | 8759 | Efectivo | — | — | — | No |
| BTC | 4h | 2189 | Efectivo | — | — | — | No |
| LINK | 1h | 8759 | Efectivo | — | — | — | No |
| LINK | 4h | 2189 | momentum-vol-12 | 7 | 1.81 | 1.4 | No |
| ETH | 1h | 8759 | Efectivo | — | — | — | No |
| ETH | 4h | 2189 | Efectivo | — | — | — | No |
| SOL | 1h | 8759 | Efectivo | — | — | — | No |
| SOL | 4h | 2189 | Efectivo | — | — | — | No |

Solo LINK 4 h llegó a tener candidato para el tramo final: momentum-vol-12.
Sus 7 operaciones finales dieron +1,81 USDT sobre la referencia de 100 USDT y
+1,40 a doble coste. No se considera validado: se exigían 20 cierres; solo 2 de
3 bloques previos fueron positivos; el límite inferior del intervalo de
expectativa fue -0,2682 USDT. Escogerlo porque el número final es positivo
introduciría selección retrospectiva.

En las otras siete series, ninguna variante superó las condiciones de
selección; sus tramos finales de estrategias se conservaron sin consultar.
Esto protege esos datos frente a la búsqueda repetida de un ganador.

Informe reproducible con hashes, fechas y ensayos completos:
`reports/grok-only-research-20261024.json`.

## Qué sí se verificó y qué falta

Han pasado 427 pruebas del backend. La compilación del frontend y las verificaciones de
navegador cubren reconexión, eventos de los chefs, bocadillos con caducidad,
llenados maker, cuenta atrás, actualización de posiciones, límites del auditor
y ausencia de respaldo a APIs de pago. Registro:
`.runtime/subscription-full-tests.log`.

La prueba histórica evalúa familias de estrategias spot con costes supuestos.
No reconstruye señales históricas de X ni mide una ventaja incremental de los
modelos nuevos. No valida scalp, cola maker exacta ni ejecuciones del bróker.
La conexión por suscripción puede tardar decenas de segundos; los stops siguen
su tarea independiente. Datos ausentes, caducados, respuestas inválidas o
límites de uso cierran la aprobación del enjambre.

No se han aumentado tamaños ni activado el modo armado para perseguir un
beneficio. El siguiente contraste válido requiere observaciones futuras
separadas por versión y costes netos, sin reutilizar este tramo final como
una validación nueva. El código funcionando y una estrategia rentable son
resultados distintos; hoy solo el primero se ha comprobado.

## Documentación oficial usada

- [Grok Build headless](https://docs.x.ai/build/cli/headless-scripting).
- [Autenticación de Codex con ChatGPT](https://learn.chatgpt.com/docs/auth).
- [Ejecuciones no interactivas de Codex](https://learn.chatgpt.com/docs/non-interactive-mode).
