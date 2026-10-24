# Objetivo de duplicar o triplicar al día: prueba extrema

**El experimento no respalda ese objetivo. Aumentar mucho el riesgo empeoró el resultado en estas muestras. El perfil extremo queda descartado para las cuentas en funcionamiento.** Se conserva el perfil agresivo anterior en simulación y MT5 DEMO; esta revisión no envía órdenes, modifica sus límites ni añade fondos.

Duplicar significa +100% diario y triplicar +200%. No se puede prometer una estrategia capaz de repetirlo. El apalancamiento amplifica las pérdidas además de las ganancias: [Investor.gov, riesgos de estrategias apalancadas](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/leveraged-investing-strategies-know-risks-using-these-advanced-investment-tools).

## Qué se probó

- Las 36 combinaciones de los siete bots y siete mercados aplicables: BTC, ETH, SOL, EURUSD, GBPUSD, SPY y NVDA.
- Un candidato nuevo de impulso en 5 minutos, en los siete mercados: ruptura de cinco velas o recuperación de EMA9, tendencia y volumen. Stop 1 ATR, objetivo 8 ATR, protección a partir de 3 R y hasta seis horas.
- Tres escenarios por serie, fijados antes de ejecutar el experimento: control a tamaño a prueba, extremo del 5% por entrada con hasta 20× y extremo del 15% con hasta 50×. Límites diarios respectivos: 6%, 25% y 50%.
- 43 series, 129 escenarios y 258 ejecuciones incluyendo costes normales y estrés a costes 1,5×. Cada cuenta parte de 100 unidades de su moneda cotizada y capitaliza únicamente el saldo disponible.

## Resultados con costes normales

| Escenario | Mediana del resultado total por serie | Peor resultado total | Mejor día observado | Días que duplican / triplican |
|---|---:|---:|---:|---:|
| Control a prueba | −0,99% | −6,82% | +1,35% | 0 / 0 |
| Extremo: 5%, hasta 20× | −17,16% | −75,62% | +16,00% | 0 / 0 |
| Extremo: 15%, hasta 50× | −42,28% | −99,59% | +48,00% | 0 / 0 |

La mediana es el valor central de los resultados de las 43 cuentas independientes **durante su ventana completa**, no una rentabilidad diaria ni una cartera combinada. No se suman importes de instrumentos o monedas diferentes.

En el extremo del 15%, solo 7 de las 43 series terminan positivas; aparecen tres liquidaciones y una cuenta termina con menos de una unidad de las 100 iniciales. El candidato nuevo de impulso no termina positivo en ninguno de los siete mercados con costes normales. Bajo estrés, el extremo del 15% tiene mediana de −34,74%, peor resultado de −96,95%, mejor día de +43,37% y tampoco duplica ni triplica en un día. Los mayores costes bloquean algunas entradas; por eso el estrés puede perder menos al ejecutar menos operaciones.

Se observan 2.373 registros de día/serie por escenario. Comparten mercados y fechas: **no son 2.373 ensayos independientes** y el cero observado no estima una probabilidad futura.

## Cómo se contabilizó

Se reutilizan los datos congelados del estudio anterior, ya conocidos, y el 30% final de cada serie, con sus indicadores calentados solo por datos pasados. Las ventanas varían por instrumento y abarcan conjuntamente del 7 de julio al 21 de octubre de 2026. No hay selección de una variante ganadora ni promoción de validaciones a partir de estos resultados.

Las decisiones del cierre se ejecutan en la siguiente apertura; el tamaño se recalcula con el saldo, el riesgo neto y el margen disponible, incluyendo la comisión de entrada. Se incluyen spread, deslizamiento, ambas comisiones y financiación supuesta. Las pérdidas nocturnas y ganancias nocturnas pertenecen al día nuevo: el rendimiento diario se calcula de cierre a cierre. Se omiten el primer y último día, potencialmente incompletos.

Se usa una posición por cuenta independiente, margen cruzado hipotético y liquidación cuando el patrimonio cae al 50% del margen inicial. Ese contrato es un supuesto, no una condición verificada del bróker. Si stop y objetivo se cruzan en una vela, se contabiliza el stop primero. Los saltos se ejecutan a la apertura disponible y no se oculta deuda ni se reinicia una cuenta agotada.

Este evaluador no reproduce íntegramente las cuentas en ejecución: excluye sentimiento, selección conjunta de mercados, cartera multiactivo, reglas de reducción tras pérdidas, profundidad y todos los costes contractuales de liquidación. Por tanto es una exploración hipotética, **no una demostración de rentabilidad futura ni una validación independiente**.

## Evidencia y comprobación

- [Métricas, días y configuraciones de las 43 series](PRUEBA_EXTREMA_2026-10-21.json).
- Curvas completas y copia de los resultados: `.runtime/extreme-review-20261021-full.json`. Su hash SHA-256 está en el informe JSON resumido; cada curva también tiene su hash.
- Evaluador reproducible: `backend/app/extreme_review.py`. No se importa desde el motor ni tiene acceso a las órdenes del bróker.
- Pruebas específicas: causalidad de la señal nueva, capitalización, límites de margen, costes, protección diaria, salto nocturno, prioridad del stop, deuda tras un salto y conteo correcto de días que realmente duplican o triplican en un caso artificial.
- 379 pruebas superadas en la suite completa, incluidas las diez nuevas del evaluador. Hash de la evidencia y conteos diarios verificados. Servicio activo y MT5 confirmado como DEMO; riesgo base conservado en 0,5%.

La falta actual de una ventaja neta consistente sigue siendo el problema principal. Cambiarla por más exposición produjo pérdidas mayores en este experimento.
