#!/usr/bin/env bash
# =============================================================================
# Installation / mise à jour sur un serveur Linux (Docker + Compose v2).
#   - Mode connecté   : construit les images localement.
#   - Mode hors ligne : charge les images du dossier images/ (paquet produit par
#                       scripts/bundle-offline.sh) — aucun accès Internet requis.
# Usage : ./scripts/install.sh
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERREUR :\033[0m %s\n' "$*" >&2; exit 1; }

# Relance une commande réseau (build, pull) en cas d'incident passager (DNS, coupure).
retry() {
  local attempt
  for attempt in 1 2 3 4 5; do
    "$@" && return 0
    warn "Échec (tentative $attempt/5) : $* — nouvel essai dans 15 s"
    sleep 15
  done
  return 1
}

gen_secret() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 24
  else head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; fi
}

command -v docker >/dev/null 2>&1 || die "Docker n'est pas installé."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 est requis (commande 'docker compose')."
docker info >/dev/null 2>&1 || die "Impossible de joindre le démon Docker (droits ? sudo ?)."

# --- 1. Configuration --------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  for var in POSTGRES_SUPERUSER_PASSWORD OWUI_DB_PASSWORD CONTRATS_INGEST_PASSWORD CONTRATS_APP_PASSWORD WEBUI_SECRET_KEY; do
    sed -i "s|^${var}=.*|${var}=$(gen_secret)|" .env
  done
  chmod 600 .env
  log "Fichier .env créé avec des secrets générés."
  warn "Renseignez maintenant les endpoints internes des modèles (LLM_*, EMBEDDING_*, RERANKER_*, OCR_*)"
  warn "dans .env (voir docs/CONNEXION_MODELES.md), puis relancez : ./scripts/install.sh"
  exit 0
fi

grep -q '=change-me$' .env && die "Des secrets valent encore 'change-me' dans .env."
set -a; . ./.env; set +a
[ "${EMBEDDING_DIM:-}" -ge 1 ] 2>/dev/null || die "EMBEDDING_DIM invalide dans .env."

# --- 2. Dossiers persistants ---------------------------------------------------
DATA_ROOT="${DATA_ROOT:-./data}"
mkdir -p "$DATA_ROOT/postgres" "$DATA_ROOT/open-webui" "$DATA_ROOT/contrats" "${INBOX_DIR:-./inbox}" exports backups
log "Données persistantes : $DATA_ROOT"

# --- 3. Images -------------------------------------------------------------------
if ls images/*.tar.gz >/dev/null 2>&1; then
  log "Chargement des images hors ligne (images/*.tar.gz)…"
  for archive in images/*.tar.gz; do docker load -i "$archive"; done
else
  log "Construction des images (accès aux registres requis)…"
  retry docker compose --profile cli build || die "Construction des images impossible (réseau ?)."
  docker image inspect "${POSTGRES_IMAGE:-pgvector/pgvector:0.8.0-pg17}" >/dev/null 2>&1 \
    || retry docker compose pull postgres || die "Téléchargement de PostgreSQL impossible."
fi

# --- 4. Démarrage ------------------------------------------------------------------
log "Démarrage de PostgreSQL…"
docker compose up -d postgres
for i in $(seq 1 60); do
  status=$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q postgres)" 2>/dev/null || echo "?")
  [ "$status" = "healthy" ] && break
  sleep 2
  [ "$i" -eq 60 ] && die "PostgreSQL ne démarre pas (docker compose logs postgres)."
done

log "Migrations du schéma métier…"
docker compose run --rm ingest db upgrade

log "Démarrage d'Open WebUI…"
docker compose up -d open-webui

log "Contrôle des endpoints modèles…"
docker compose run --rm ingest check || warn "Certains contrôles ont échoué : vérifiez .env puis 'make check'."

log "Installation terminée : http://$(hostname -I 2>/dev/null | awk '{print $1}'):${OWUI_PORT:-3000}"
log "Étapes suivantes : README.md, section « Mise en service »."
