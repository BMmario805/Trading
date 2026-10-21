#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
  echo 'Ejecutar con sudo.' >&2
  exit 1
fi
TRADING_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [[ "$TRADING_ROOT" != /srv/mesa-siete ]]; then
  echo 'Instalar el paquete en /srv/mesa-siete.' >&2
  exit 1
fi
. /etc/os-release
if [[ "$ID" != ubuntu ]]; then
  echo 'Este instalador requiere Ubuntu.' >&2
  exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl python3
if ! docker compose version >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
install -d -o 10001 -g 10001 -m 0700 "$TRADING_ROOT/state/runtime" "$TRADING_ROOT/state/data" "$TRADING_ROOT/state/backups"
chown -R 10001:10001 "$TRADING_ROOT/state"
chmod -R go-rwx "$TRADING_ROOT/state"
cd "$TRADING_ROOT"
docker compose config --quiet
docker compose build --pull
cat > /etc/systemd/system/mesa-siete-backup.service <<EOF
[Unit]
Description=Copia consistente del estado de Mesa Siete
Requires=docker.service
After=docker.service
[Service]
Type=oneshot
WorkingDirectory=$TRADING_ROOT
ExecStart=/usr/bin/docker compose exec -T trading python /app/deploy/state_bundle.py backup --root /app --data-db /var/lib/trading/data.db --output /backups --include-gemini
EOF
cat > /etc/systemd/system/mesa-siete-backup.timer <<'EOF'
[Unit]
Description=Copia diaria de Mesa Siete
[Timer]
OnCalendar=*-*-* 03:15:00 UTC
RandomizedDelaySec=300
Persistent=true
[Install]
WantedBy=timers.target
EOF
cat > /etc/systemd/system/mesa-siete-watchdog.service <<EOF
[Unit]
Description=Recuperación del motor de Mesa Siete
Requires=docker.service
After=docker.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $TRADING_ROOT/deploy/watchdog.py
EOF
cat > /etc/systemd/system/mesa-siete-watchdog.timer <<'EOF'
[Unit]
Description=Supervisión del motor de Mesa Siete
[Timer]
OnBootSec=3min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
EOF
# Solo iniciar después de transferir la copia definitiva y detener la ejecución local.
if [[ "${1:-}" == --start ]]; then
  docker compose up -d --wait --wait-timeout 240
  systemctl daemon-reload
  systemctl enable --now mesa-siete-watchdog.timer mesa-siete-backup.timer
  systemctl start mesa-siete-backup.service
  curl --fail --silent http://127.0.0.1:8080/api/health
else
  systemctl daemon-reload
  echo 'Imagen preparada. Para activar tras la migración: sudo bash deploy/setup-ubuntu.sh --start'
fi
