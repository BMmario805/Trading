# Mesa Siete — 14 de octubre de 2026

La nueva página inicial reproduce la organización del ejemplo: un selector y
seis roles especializados, curva de patrimonio, velas, lista de mercados,
decisiones e historial. Se ha comprobado con una llamada real a Gemini y con
datos públicos del mercado. No se han contratado otras APIs.

## Qué está funcionando

| Rol | Responsable | Función |
|---|---|---|
| Selector | Gemini 3.5 Flash-Lite | Elige un mercado elegible o efectivo a partir de volumen, spread y variación diaria. |
| Tape | Reglas locales | Lee velas cerradas de 15 minutos, EMA20/50, RSI y ATR. |
| Price | Reglas locales | Exige tendencia, retroceso y recuperación; bloquea entradas demasiado alejadas. |
| Wire | Reglas locales | Busca el enlace exacto de X en una página oficial previamente registrada. |
| Size | Reglas locales | Limita el importe al entrar a un tercio del patrimonio y calcula riesgo objetivo del 1%. |
| Exec | Libro simulado | Registra una compra spot con costes y evita duplicar una entrada por vela. |
| Risk | Reglas locales | Gestiona stop, objetivo, trailing, pérdida de tendencia, tiempo y pérdida diaria. |

Son siete roles, con una sola IA. No son siete llamadas simultáneas ni bots
Grok. Gemini no dispone de herramientas para enviar órdenes ni recibe la
clave de ninguna cuenta de negociación.

El universo contiene hasta 81 pares spot USDT de Binance ordenados por volumen,
excluyendo las principales monedas estables y productos apalancados. La
elegibilidad exige los filtros de liquidez y spread existentes y una referencia
de identidad registrada. Inicialmente hay referencias para ETH, SOL, BNB,
LINK, SUI, AVAX y DOGE. Los otros mercados siguen visibles con el motivo del
descarte. No se simula haber comprobado su identidad.

Wire corrobora un enlace desde el sitio registrado. No demuestra propiedad
de la cuenta, ausencia de estafas, autenticidad de cualquier publicación ni
seguridad de contratos. Si falta el enlace, falla la consulta o cambia el
dominio, impide la entrada. Para ETH se coteja @ethdotorg desde
[ethereum.org](https://ethereum.org/community/), la cuenta enlazada por ese
sitio. Este piloto no opera tokens DEX ni analiza contratos de memecoins.

## Cuenta y controles

El libro empieza con **1.000 USDT virtuales** y permite una posición spot a la
vez, sin apalancamiento. Está separado de las cuentas y bots anteriores.
No existe pasarela de órdenes reales en esta mesa.

Cada lado descuenta comisión del 0,1% y deslizamiento supuesto del 0,02%.
Las compras usan ask y las ventas bid. El tamaño respeta el paso y mínimo del
mercado, el efectivo disponible y un máximo del 10% de la cantidad publicada
al mejor ask. El deslizamiento real y la profundidad completa pueden diferir.

El stop inicial queda a 1,5 ATR y el objetivo a 2,5 ATR. Al ganar 1R se activa
trailing. También se cierra al perder la tendencia confirmada o alcanzar seis
horas. El riesgo objetivo incluye costes y deslizamiento supuesto al stop;
los gaps pueden provocar una pérdida mayor. Un tercio es el límite del
capital asignado al entrar, no la pérdida permitida ni un límite que se
reequilibre continuamente cuando sube el precio.

Una caída diaria del 5% del patrimonio, incluyendo la posición marcada a
mercado, bloquea entradas y solicita cierre con cotización fresca. El día se
mide en UTC. No se inventan precios si la fuente está caída. Pausar detiene
nuevas compras y mantiene los controles de una posición abierta. «Cerrar
posición» pausa y solicita su cierre simulado en el siguiente ciclo con
precio válido.

Los controles se revisan cada diez segundos con una fuente compartida que
puede guardar cotizaciones hasta treinta segundos; el análisis de entrada se
revisa aproximadamente cada minuto. Las cotizaciones mayores de sesenta
segundos no permiten entradas ni cierres. No es ejecución de baja latencia.

## Selección, comparación e historial

La primera selección real examinó 81 mercados y eligió **ETHUSDT**. Consumió
4.107 tokens de entrada y 84 de salida: coste estimado **0,0014421 USD** con
las tarifas configuradas. Esa cifra corresponde a esa selección concreta;
no incluye infraestructura ni representa una garantía de coste futuro.
La confianza del modelo es clasificación, no probabilidad de ganar.

La selección permanece mientras exista una posición. Si está plana y activa,
se solicita una nueva elección al cumplirse seis horas, respetando los límites
compartidos de Gemini. Un fallo o una selección de efectivo mantiene la mesa
sin nuevas entradas hasta que pueda obtener una elección válida.

La referencia «comprar y mantener» comienza cuando Wire corrobora el primer
mercado elegido. Invierte todo el capital inicial en ese activo con los
mismos costes y permanece fija aunque el selector cambie después de moneda.
La mesa asigna como máximo un tercio por entrada: la comparación no iguala
exposición ni riesgo. El nombre del activo de referencia se muestra en el
panel. La curva visible conserva las últimas 24 horas; el registro persiste.

`.runtime/desk.sqlite` guarda efectivo, posición, evidencia de entrada,
selección, operaciones, eventos y curva. Al reiniciar recupera la cuenta y
los stops; una clave única por mercado y vela evita duplicar entradas.
Las estadísticas se calculan sobre todos los cierres, aunque la tabla visible
muestre solo las últimas cien operaciones. `.runtime/gemini.sqlite` conserva
las llamadas y el consumo compartido.

## Arranque y comprobaciones

Abrir [la mesa local](http://localhost:5173). «Mercados y laboratorio» permite
volver al panel anterior. `start.ps1` abre el proyecto; `start-background.ps1`
inicia los servicios sin ventanas y reutiliza los procesos activos.
El acceso en Inicio de Windows arranca también esta mesa al iniciar sesión.
El PC debe estar encendido, conectado y sin suspensión. No se ha probado un
reinicio completo de Windows.

**101 pruebas automáticas pasan**, el frontend compila y el análisis estático
no presenta errores (conserva tres advertencias anteriores). Se verificaron
límites de tamaño, identidad engañosa, comisión y balance, recuperación,
duplicados, precios antiguos, pausa, cambio de tendencia y bloqueo diario.
La conexión real y el panel se comprobaron por separado de esas pruebas.

Esto confirma funcionamiento del software, no rentabilidad. Todavía no hay
una evaluación histórica completa y fuera de muestra de esta mesa ni
resultados suficientes en ejecución simulada. Las cifras +67% y +26% del
vídeo no se pudieron verificar con un registro auditado. Para considerar
capital real harían falta resultados con costes, pérdidas y estabilidad en
periodos nuevos, además de una integración de ejecución específica.
