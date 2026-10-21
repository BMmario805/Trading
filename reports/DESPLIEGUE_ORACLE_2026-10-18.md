# Despliegue Oracle — 18 de octubre de 2026

**Estado: preparado y probado localmente; pendiente de capacidad del proveedor.**
El motor continúa en el ordenador local. No se ha activado una instancia remota
ni se ha cambiado la cuenta de Oracle a modalidad de pago.

## Cuenta y recursos revisados

- Región principal: Madrid, `eu-madrid-1`, con un único dominio de disponibilidad.
- Cuenta Free Tier. Cuota Ampere disponible: 2 OCPU y 12 GB.
- Almacenamiento gratuito regional: 200 GB disponibles y 0 GB usados.
- Disco de arranque elegido: tamaño por defecto, unos 47 GB.
- El estimador de Oracle muestra 1,85 €/mes para el disco; la configuración
  entra en las cuotas gratuitas revisadas. No se ha contratado una tarifa de pago.
- Se autorizó la creación de `mesa-siete-crypto` y el registro de su clave SSH.
  La clave privada permanece en `.runtime/oracle-access/`, fuera del control de versiones.

## Intentos y bloqueo

Tres intentos devueltos por Oracle con `Out of capacity` en AD-1:

1. Ampere A1: 2 OCPU y 12 GB, Ubuntu ARM 24.04 Minimal.
2. E2.1.Micro: Ubuntu 24.04 Minimal, 1 GB de RAM.
3. Ampere A1: 1 OCPU y 6 GB, Ubuntu ARM 24.04 Minimal.

La selección de dominio de fallo estaba en automática. La lista de redes tras
los primeros intentos seguía mostrando únicamente la red anterior de la cuenta;
no hay indicios de que el intento fallido creara la red nueva de Mesa Siete.
La falta de capacidad es distinta de la cuota: disponer de cuota gratuita
no garantiza que Oracle tenga máquinas físicas disponibles.

## Trabajo listo

- Imagen para Linux AMD64/ARM64 con el panel compilado y un único proceso de trading.
- Estado y bases SQLite persistentes fuera de la imagen, copias online verificadas
  mediante `integrity_check` y un paquete local de migración sin contraseñas MT5.
- Panel ligado a localhost en el servidor para acceso privado por túnel SSH.
- Comprobación de salud de la API y de las tareas de ejecución y riesgo.
- Reinicio automático, vigilante, límites de logs y copias diarias con retención de 14.
- MetaTrader desactivado en Linux; la integración del terminal de Windows se conserva localmente.

## Validación y límites

- 280 pruebas Python correctas, incluidas recuperación de SQLite/WAL y supervisión.
- Panel compilado correctamente; configuración Compose validada.
- Descarga comprobada de las librerías nativas principales para Python 3.12 ARM64.
- Docker Desktop local no tiene el motor Linux arrancado: la construcción completa
  de la imagen y las pruebas de reinicio en Linux quedan pendientes del servidor.
- El paquete inicial conserva una copia del estado mientras el motor local sigue
  funcionando; al completar la migración hay que detener el local y tomar una copia
  definitiva antes de activar el remoto. No ejecutar las dos copias simultáneamente.
- Las copias remotas diarias previstas estarán en el mismo disco. Se necesita
  copia externa para cubrir la pérdida del volumen; la exportación local se conserva.

Instrucciones: [deploy/README.md](../deploy/README.md).
