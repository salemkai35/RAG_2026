#!/usr/bin/env bash
# =============================================================================
# Restauration d'une sauvegarde produite par scripts/backup.sh.
# Usage : ./scripts/restore.sh backups/AAAAMMJJ-HHMMSS
#
# Sur un NOUVEAU serveur : copier d'abord <sauvegarde>/env.backup en .env
# (mêmes mots de passe), lancer ./scripts/install.sh, puis ce script.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="${1:?usage : restore.sh <dossier_de_sauvegarde>}"
[ -f "$SRC/contrats.dump" ] || { echo "Sauvegarde invalide : $SRC"; exit 1; }
( cd "$SRC" && sha256sum -c SHA256SUMS --quiet ) || { echo "Empreintes invalides."; exit 1; }

set -a; . ./.env; set +a
DATA_ROOT="${DATA_ROOT:-./data}"

echo "ATTENTION : les bases et fichiers actuels vont être REMPLACÉS par $SRC."
read -r -p "Taper OUI pour continuer : " answer
[ "$answer" = "OUI" ] || { echo "Annulé."; exit 1; }

docker compose stop open-webui
docker compose up -d postgres
sleep 5

for db in openwebui contrats; do
  echo "==> Restauration de la base $db"
  docker compose exec -T postgres pg_restore -U postgres -d "$db" --clean --if-exists < "$SRC/$db.dump"
done

echo "==> Restauration des fichiers"
rm -rf "$DATA_ROOT/open-webui" "$DATA_ROOT/contrats"
tar -C "$DATA_ROOT" -xzf "$SRC/fichiers.tar.gz"

docker compose up -d open-webui
docker compose run --rm ingest check || true
echo "==> Restauration terminée."
