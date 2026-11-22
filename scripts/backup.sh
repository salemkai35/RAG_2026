#!/usr/bin/env bash
# =============================================================================
# Sauvegarde complète : deux bases PostgreSQL + fichiers (Open WebUI, documents)
# + configuration. Usage : ./scripts/backup.sh [dossier_destination]
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

DATA_ROOT="${DATA_ROOT:-./data}"
DEST="${1:-backups}/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"
chmod 700 "$DEST"

echo "==> Dump des bases"
docker compose exec -T postgres pg_dumpall -U postgres --roles-only > "$DEST/roles.sql"
docker compose exec -T postgres pg_dump -U postgres -Fc openwebui > "$DEST/openwebui.dump"
docker compose exec -T postgres pg_dump -U postgres -Fc contrats  > "$DEST/contrats.dump"

echo "==> Archive des fichiers"
tar -C "$DATA_ROOT" -czf "$DEST/fichiers.tar.gz" open-webui contrats

cp .env "$DEST/env.backup"
chmod 600 "$DEST/env.backup"
( cd "$DEST" && sha256sum ./* > SHA256SUMS )
echo "==> Sauvegarde terminée : $DEST"
echo "    (contient les secrets du fichier .env : à stocker dans un emplacement protégé)"
