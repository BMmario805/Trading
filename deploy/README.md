# Mesa Siete en Oracle Cloud

Objetivo: una instancia Ubuntu ARM 24.04 en Madrid, VM.Standard.A1.Flex,
2 OCPU y 12 GB como máximo, disco de arranque de unos 47–50 GB. Mantenerse
dentro de Always Free y comprobar el almacenamiento ya utilizado en la cuenta.
El presupuesto de la consola puede mostrar el disco a precio de lista: no
dar por confirmada la gratuidad hasta revisar la cuota y la región principal.

## Preparación y migración

1. Verificar la clave y huella SSH del servidor y su conectividad con las fuentes.
2. Crear el paquete con `deploy/state_bundle.py bundle --output <directorio-nuevo>`.
   `--include-gemini` añade la clave existente de Gemini: el paquete debe tratarse
   como secreto y transferirse solo al servidor privado mediante SSH.
3. Transferir y extraer el paquete en `/srv/mesa-siete`. En una instalación nueva,
   ejecutar `sudo bash deploy/setup-ubuntu.sh` para construir, sin arrancar bots.
4. Detener la ejecución local antes de la copia definitiva de estado. Repetir
   la exportación SQLite con el motor detenido y transferir solo `state/`.
   No sobrescribir el estado de una instalación remota que ya esté operando.
5. Activar con `sudo bash deploy/setup-ubuntu.sh --start`. Verificar los tres
   escritorios, los siete bots, la conservación de posiciones y el primer backup.
6. Probar un reinicio del contenedor y comprobar de nuevo saldos y posiciones.
   Si falla la migración antes del arranque remoto, reanudar el motor local.

## Acceso

El servicio escucha únicamente en `127.0.0.1:8080` en el servidor. Para consultar
el panel: `ssh -i <clave> -N -L 127.0.0.1:5180:127.0.0.1:8080 ubuntu@<servidor>`
y abrir `http://localhost:5180`. Al cerrar la conexión, el servidor sigue operando.
No abrir 8000, 8080 ni 5173 al público. Un dominio público necesita autenticación
y HTTPS antes de habilitarlo. Un único contenedor y un único worker evitan
duplicar los motores de trading.

## Operación y límites

- `docker compose ps` y `/api/health` verifican API, tareas y progreso de los
  bucles de mesas. Un monitor vivo con latido vencido devuelve estado degradado.
- Docker reinicia procesos caídos; el vigilante recupera un motor con fallos
  de supervisión persistentes. No reinicia un contenedor detenido manualmente.
- Copias SQLite diarias en `state/backups`, con las últimas 14 conservadas.
  Estas copias están en el mismo disco: hace falta una copia externa para
  recuperarse si se pierde el volumen o la cuenta. La exportación local inicial
  queda conservada por separado.
- Los logs tienen límite de tamaño. Imágenes y estado permanecen separados.
- No escalar réplicas ni arrancar el motor local simultáneamente sobre otra copia.
- MetaTrader 5 queda desactivado en Linux. No se incluyen su contraseña ni el
  fichero `mt5-demo.json`; se conserva su base histórica para una futura recuperación.
- El modo sigue siendo simulado. El despliegue no añade ejecución con dinero real.
- Always Free está sujeto a capacidad, límites de uso y recuperación por inactividad.

Referencias: [Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm),
[Docker en Ubuntu](https://docs.docker.com/engine/install/ubuntu/).

## Ensayo de recuperación sin activar motores

La copia incluye `backend/data.db` y los libros presentes de cripto, forex,
acciones, laboratorio, MT5, swarm, registro de investigación, coordinación y
auditoría de decisiones. También conserva los snapshots publicados de
`.runtime/research-data/<sha256>.json` y comprueba las referencias del registro
de investigación. Usa la API de backup de SQLite, incorpora transacciones
confirmadas que sigan en WAL y genera cada libro como un archivo independiente,
sin necesitar `-wal` ni `-shm`. La consistencia es por libro: una copia mientras
funcionan los motores no constituye una transacción atómica entre todos ellos.
Para una migración definitiva hay que detener primero todos los escritores.

Desde la raíz del proyecto, con Python 3.13:

```text
python deploy/state_bundle.py backup --output <directorio-copias>
python deploy/state_bundle.py verify --snapshot <directorio-copias>/snapshot-<fecha>
python deploy/state_bundle.py restore --snapshot <directorio-copias>/snapshot-<fecha> --output <directorio-nuevo-de-ensayo>
```

`verify` comprueba lista permitida, SHA-256 e integridad SQLite; rechaza rutas
fuera de la copia, duplicados, enlaces, junctions y archivos adicionales,
incluidos sidecars no verificados. Los hashes detectan cambios respecto del
manifiesto; no autentican a su autor. Conservar la copia y el manifiesto en un
almacenamiento privado fiable. `gemini.env` solo se incluye con
`--include-gemini`; `mt5-demo.json` y sus credenciales no se exportan.
El archivo de datasets se limita a 1.024 snapshots, 8 MiB por archivo y 256 MiB
en conjunto. Superar un límite o perder una referencia aborta la comprobación;
no se descartan snapshots para lograr una copia aparentemente completa.

`restore` exige un destino que no exista, fuera de la copia, y vuelve a verificar
los archivos copiados. Crea únicamente un directorio de ensayo: no inicia
servicios ni cambia la ubicación que utilizan los motores. Si falla una copia,
el destino parcial no debe utilizarse como estado recuperado.

Antes de una activación manual, comparar saldos, posiciones, reservas e intentos
pendientes, y verificar la continuidad de los registros de investigación y
decisiones. Conciliar cualquier estado incierto con su origen antes de admitir
entradas. Una integridad SQLite correcta no prueba esa conciliación económica.
Medir y registrar duración de copia/restauración y antigüedad del último estado
recuperado para fijar los objetivos de recuperación. Las pruebas automatizadas
usan libros temporales con recibos conocidos; no sustituyen ese ensayo operativo.

## Dependencias y comprobaciones reproducibles

`backend/requirements.lock` fija las versiones directas y transitivas del
entorno Python 3.13 comprobado en Windows, incluidas las herramientas de prueba.
`requirements-cloud.txt` reutiliza exactamente ese archivo y conserva el
marcador que limita MetaTrader a Windows. El Dockerfile usa Python 3.13. No se
deben actualizar paquetes automáticamente durante un despliegue: una renovación
del lock exige revisión y repetir las comprobaciones.

El flujo `.github/workflows/ci.yml` define pruebas del backend en Windows y
Ubuntu con Python 3.13, comprobación de dependencias y construcción/análisis del
panel con Node 24 y `npm ci`. La ejecución local equivalente, sin reinstalar,
es `python -m pip check` y `python -m pytest tests -q` dentro de `backend`, más
`npm run build` y `npm run lint` dentro de `frontend`. Un workflow guardado no
confirma que GitHub lo haya ejecutado; comprobar su resultado al publicar.

Los tags base de Docker y las imágenes de CI siguen siendo actualizables.
Para reconstrucción bit a bit hay que fijar además sus digests y conservar los
artefactos de dependencias de la versión validada. La matriz actual no prueba
la imagen ARM de Oracle: construirla y ensayarla por separado antes de migrar.

## Reintentos acotados de capacidad

`retry_oracle.py` usa el SDK oficial OCI (dependencia opcional `oci`, fuera del
runtime de trading). Completar una copia local de `oracle-plan.example.json`
con compartimento, AD, subred existente, clave pública e imágenes Ubuntu
distintas para ARM y AMD64. El plan admite únicamente A1 de 1 OCPU/6 GB y
E2.1.Micro, con disco de 50 GB. Revisar cuotas y coste de la cuenta; el script
no confirma Always Free ni amplía cuotas.

`python deploy/retry_oracle.py --plan .runtime/oracle-plan.json` valida sin
llamadas al proveedor. Añadir `--apply` solicita una instancia. Puede invocarse
desde un planificador cada quince minutos; no hay ningún planificador instalado
por esta modificación. Cada ejecución realiza como máximo un intento de alta.
El diario `.runtime/oracle-retry.sqlite` evita solicitudes simultáneas,
conserva el token y detiene reintentos tras éxito o respuesta incierta. Solo una
falta de capacidad confirmada permite alternar shape en la siguiente ejecución.

No borrar el diario para sortear una respuesta incierta: reconciliar primero
instancias y volúmenes en OCI. Crear una instancia no inicia la migración ni el
trading remoto; seguir el procedimiento de copia definitiva y parada local.
