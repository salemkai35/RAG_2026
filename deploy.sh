#!/usr/bin/env bash
# =============================================================================
#  Contrats RAG – déploiement en une commande sur un serveur Ubuntu / Debian vierge.
#
#    git clone <depot> contrats-rag && cd contrats-rag
#    sudo ./deploy.sh
#
#  Étapes (toutes idempotentes : le script peut être relancé sans risque) :
#    1. vérifie le système (Ubuntu/Debian x86_64, droits root) ;
#    2. installe les prérequis absents : Docker Engine, Compose v2, git, curl, openssl, make ;
#    3. active Docker au démarrage et donne l'accès Docker à l'utilisateur appelant ;
#    4. crée .env (secrets aléatoires) puis demande les adresses des API de modèles ;
#    5. crée les dossiers persistants, construit les images, crée le réseau Docker,
#       démarre PostgreSQL, applique les migrations, démarre Open WebUI, contrôle les modèles
#       (délégué à scripts/install.sh).
#
#  Options :
#    --env-file FICHIER   utiliser un .env déjà préparé (ex. repris d'une sauvegarde)
#    --non-interactive    ne rien demander : valeurs lues dans l'environnement
#                         (LLM_BASE_URL, LLM_MODEL, EMBEDDING_BASE_URL, EMBEDDING_MODEL,
#                          EMBEDDING_DIM, RERANKER_API, AI_ALLOWED_HOSTS, OWUI_PORT…)
#    --skip-prereqs       ne pas installer de paquets (Docker déjà présent)
#    -h, --help           aide
#
#  Le script ne supprime jamais de données : un .env ou un dossier data/ existant est conservé.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ✔\033[0m  %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERREUR :\033[0m %s\n' "$*" >&2; exit 1; }

ENV_SOURCE=""
INTERACTIVE=1
SKIP_PREREQS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file)        ENV_SOURCE="${2:?--env-file attend un chemin}"; shift 2 ;;
    --non-interactive) INTERACTIVE=0; shift ;;
    --skip-prereqs)    SKIP_PREREQS=1; shift ;;
    -h|--help)         sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)                 die "Option inconnue : $1 (voir --help)" ;;
  esac
done
{ : < /dev/tty; } 2>/dev/null || INTERACTIVE=0   # pas de terminal (CI, cron) : mode automatique

# Utilisateur réel (celui qui a tapé sudo) : propriétaire des fichiers créés.
REAL_USER="${SUDO_USER:-$(id -un)}"
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || die "Lancez ce script en root ou installez sudo."
  SUDO="sudo"
fi

# --- 1. Système ----------------------------------------------------------------
log "1/5 Vérification du système"
[ -r /etc/os-release ] || die "/etc/os-release introuvable : système non pris en charge."
. /etc/os-release
case "${ID:-}:${ID_LIKE:-}" in
  ubuntu:*|debian:*|*:*debian*|*:*ubuntu*) ok "Système : ${PRETTY_NAME:-$ID}" ;;
  *) warn "Système ${PRETTY_NAME:-inconnu} non testé : l'installation des paquets peut échouer." ;;
esac
[ "$(uname -m)" = "x86_64" ] || warn "Architecture $(uname -m) non testée (images prévues pour x86_64)."
mem_gb=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 0)
[ "${mem_gb:-0}" -ge 7 ] && ok "Mémoire : ${mem_gb} Go" || warn "Mémoire : ${mem_gb} Go (8 Go minimum conseillés)."
free_gb=$(df -Pk "$PROJECT_DIR" | awk 'NR==2 {printf "%d", $4/1024/1024}')
[ "${free_gb:-0}" -ge 15 ] && ok "Disque libre : ${free_gb} Go" || warn "Disque libre : ${free_gb} Go (15 Go minimum)."

# --- 2. Prérequis ------------------------------------------------------------------
log "2/5 Prérequis (Docker, Compose v2, outils)"
apt_install() {
  export DEBIAN_FRONTEND=noninteractive
  $SUDO apt-get update -y
  $SUDO apt-get install -y --no-install-recommends "$@"
}
if [ "$SKIP_PREREQS" -eq 0 ]; then
  command -v apt-get >/dev/null 2>&1 || die "apt-get absent : installez Docker manuellement puis relancez avec --skip-prereqs."
  missing=()
  for tool in git curl openssl make; do command -v "$tool" >/dev/null 2>&1 || missing+=("$tool"); done
  [ ${#missing[@]} -eq 0 ] || apt_install ca-certificates "${missing[@]}"

  if ! command -v docker >/dev/null 2>&1; then
    log "Installation de Docker Engine (paquets de la distribution)…"
    apt_install docker.io
  fi
  if ! docker compose version >/dev/null 2>&1; then
    log "Installation du plugin Docker Compose v2…"
    apt_install docker-compose-v2 2>/dev/null || apt_install docker-compose-plugin 2>/dev/null \
      || die "Compose v2 introuvable dans les dépôts : installez 'docker-compose-plugin' (dépôt Docker officiel ou miroir interne)."
  fi
fi
command -v docker >/dev/null 2>&1 || die "Docker n'est pas installé."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 est requis."

# Démon Docker actif et lancé au démarrage du serveur.
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  $SUDO systemctl enable --now docker >/dev/null 2>&1 || warn "Impossible d'activer docker via systemd."
else
  $SUDO service docker start >/dev/null 2>&1 || true
fi
for i in $(seq 1 30); do $SUDO docker info >/dev/null 2>&1 && break; sleep 1; done
$SUDO docker info >/dev/null 2>&1 || die "Le démon Docker ne répond pas (journalctl -u docker)."
ok "$(docker --version)"
ok "$(docker compose version)"

# Accès Docker sans sudo pour l'utilisateur réel (effectif à sa prochaine connexion).
if [ "$REAL_USER" != "root" ] && ! id -nG "$REAL_USER" | tr ' ' '\n' | grep -qx docker; then
  $SUDO usermod -aG docker "$REAL_USER" && ok "Utilisateur $REAL_USER ajouté au groupe docker (reconnexion nécessaire)."
fi
# Pendant ce script, on passe par sudo si l'utilisateur n'a pas encore les droits.
DOCKER_SUDO=""
docker info >/dev/null 2>&1 || DOCKER_SUDO="$SUDO"

chmod +x scripts/*.sh docker/postgres/init/*.sh 2>/dev/null || true

# --- 3. Configuration (.env) -----------------------------------------------------------
log "3/5 Configuration"
# Écrit KEY=VALUE dans .env (remplace la ligne existante, sinon l'ajoute).
set_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  KEY="$key" VALUE="$value" awk '
    BEGIN { k = ENVIRON["KEY"]; v = ENVIRON["VALUE"]; done = 0 }
    $0 ~ "^" k "=" { print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }' .env > "$tmp"
  cat "$tmp" > .env && rm -f "$tmp"
}
get_env() { awk -F= -v k="$1" '$1 == k { sub(/^[^=]*=/, ""); print; exit }' .env; }
ask() {  # ask VARIABLE "Question" : propose la valeur actuelle (ou celle de l'environnement)
  local var="$1" question="$2" current answer
  current="${!var:-$(get_env "$var")}"
  if [ "$INTERACTIVE" -eq 1 ]; then
    case "$var" in
      *API_KEY)  # secret : saisie masquée, valeur actuelle jamais affichée
        read -r -s -p "    $question [$([ -n "$current" ] && echo '••• inchangée' || echo 'vide')] : " answer < /dev/tty || true
        echo ;;
      *) read -r -p "    $question [$current] : " answer < /dev/tty || true ;;
    esac
    answer="${answer:-$current}"
  else
    answer="$current"
  fi
  set_env "$var" "$answer"
}
host_of() { printf '%s' "$1" | sed -E 's#^[a-zA-Z]+://##; s#[/:].*$##'; }

NEW_ENV=0
if [ -n "$ENV_SOURCE" ]; then
  [ -f "$ENV_SOURCE" ] || die "Fichier introuvable : $ENV_SOURCE"
  if [ -f .env ] && ! cmp -s "$ENV_SOURCE" .env; then
    cp .env ".env.avant-deploy.$(date +%Y%m%d-%H%M%S)"
    warn "Ancien .env sauvegardé (.env.avant-deploy.*)."
  fi
  cp "$ENV_SOURCE" .env
  ok ".env repris de $ENV_SOURCE"
elif [ ! -f .env ]; then
  # Le premier passage de install.sh crée .env et génère les 5 secrets.
  $DOCKER_SUDO ./scripts/install.sh >/dev/null
  NEW_ENV=1
  ok ".env créé, secrets générés"
else
  ok ".env existant conservé"
fi
chmod 600 .env

if [ "$NEW_ENV" -eq 1 ] || [ "$INTERACTIVE" -eq 1 ]; then
  if [ "$INTERACTIVE" -eq 1 ]; then
    echo "    Adresses des API internes de modèles (format OpenAI, avec /v1)."
    echo "    Entrée = garder la valeur proposée. Détails : docs/CONNEXION_MODELES.md"
  fi
  ask LLM_BASE_URL       "URL du LLM (ex. http://llm.interne:8000/v1)"
  ask LLM_MODEL          "Nom du modèle LLM (GET /v1/models)"
  ask LLM_API_KEY        "Clé API du LLM (vide si aucune)"
  ask EMBEDDING_BASE_URL "URL des embeddings"
  ask EMBEDDING_MODEL    "Nom du modèle d'embeddings"
  ask EMBEDDING_DIM      "Dimension des embeddings (1024 bge-m3, 768 nomic)"
  ask RERANKER_API       "Reranker : cohere | tei | none"
  if [ "$(get_env RERANKER_API)" != "none" ]; then
    ask RERANKER_BASE_URL "URL du reranker"
    ask RERANKER_MODEL    "Nom du modèle de reranking"
  fi
  # Liste blanche par défaut : les hôtes des URL saisies.
  hosts="$(for v in LLM_BASE_URL EMBEDDING_BASE_URL RERANKER_BASE_URL VISION_BASE_URL; do
             [ "$v" = RERANKER_BASE_URL ] && [ "$(get_env RERANKER_API)" = "none" ] && continue
             u="$(get_env "$v")"
             if [ -n "$u" ]; then host_of "$u"; echo; fi
           done | sort -u | paste -sd, -)" || hosts=""
  current_hosts="$(get_env AI_ALLOWED_HOSTS)"
  if [ -z "${AI_ALLOWED_HOSTS:-}" ] && { [ "$NEW_ENV" -eq 1 ] || [ -z "$current_hosts" ]; }; then
    set_env AI_ALLOWED_HOSTS "$hosts"
  fi
  ask AI_ALLOWED_HOSTS   "Hôtes IA autorisés (liste blanche)"
  ask OWUI_PORT          "Port de l'interface web"
  ask DATA_ROOT          "Dossier des données persistantes"
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  [ -n "$(get_env WEBUI_URL)" ] || set_env WEBUI_URL "http://${ip:-localhost}:$(get_env OWUI_PORT)"
  ask WEBUI_URL          "Adresse de l'interface pour les utilisateurs"
fi

# Autres réglages repris de l'environnement s'ils y sont définis (utile en mode automatique).
for v in COMPOSE_PROJECT_NAME APP_VERSION OWUI_BIND INBOX_DIR TZ OCR_ENGINE OCR_LANGUAGES \
         EMBEDDING_QUERY_PREFIX EMBEDDING_DOCUMENT_PREFIX DEFAULT_CONTRACT_ACCESS ENABLE_SIGNUP \
         VISION_BASE_URL VISION_MODEL; do
  [ -n "${!v:-}" ] && set_env "$v" "${!v}"
done
if [ "$INTERACTIVE" -eq 0 ] && [ "$NEW_ENV" -eq 0 ] && [ -z "$ENV_SOURCE" ]; then
  for v in LLM_BASE_URL LLM_MODEL LLM_API_KEY EMBEDDING_BASE_URL EMBEDDING_MODEL EMBEDDING_DIM \
           RERANKER_API RERANKER_BASE_URL RERANKER_MODEL AI_ALLOWED_HOSTS OWUI_PORT DATA_ROOT WEBUI_URL; do
    [ -n "${!v:-}" ] && set_env "$v" "${!v}"
  done
fi

# Contrôles de cohérence avant de lancer quoi que ce soit.
grep -q '=change-me$' .env && die "Des secrets valent encore 'change-me' dans .env."
grep -Eq '^(LLM|EMBEDDING)_BASE_URL=.*interne\.local' .env \
  && warn "Les URL des modèles sont encore les exemples (*.interne.local) : 'check' échouera tant qu'elles ne sont pas corrigées."
[ "$(get_env EMBEDDING_DIM)" -ge 1 ] 2>/dev/null || die "EMBEDDING_DIM invalide dans .env."
[ "$REAL_USER" != "root" ] && $SUDO chown "$REAL_USER":"$(id -gn "$REAL_USER")" .env 2>/dev/null || true
ok "Configuration valide ($(grep -c '^[A-Z]' .env) variables)"

# --- 4. Dossiers, réseau, volumes ---------------------------------------------------------
log "4/5 Dossiers persistants et ressources Docker"
DATA_ROOT_VAL="$(get_env DATA_ROOT)"; DATA_ROOT_VAL="${DATA_ROOT_VAL:-./data}"
INBOX_VAL="$(get_env INBOX_DIR)";     INBOX_VAL="${INBOX_VAL:-./inbox}"
$SUDO mkdir -p "$DATA_ROOT_VAL"/{postgres,open-webui,contrats} "$INBOX_VAL" exports backups
[ "$REAL_USER" != "root" ] && $SUDO chown "$REAL_USER" "$INBOX_VAL" exports backups 2>/dev/null || true
ok "Données : $DATA_ROOT_VAL (bases, fichiers) · dépôt des documents : $INBOX_VAL"
# Validation de compose.yml avec ce .env ; le réseau privé de l'instance est créé par
# 'docker compose up' (PostgreSQL n'y est joignable que par les conteneurs).
$DOCKER_SUDO docker compose config -q || die "compose.yml invalide avec ce .env."
ok "compose.yml valide ; réseau Docker : $($DOCKER_SUDO docker compose config | awk '/^name:/ {print $2}')_default"

# --- 5. Images, démarrage, migrations, contrôles --------------------------------------------
log "5/5 Installation (images, PostgreSQL, migrations, Open WebUI, contrôles)"
$DOCKER_SUDO ./scripts/install.sh

echo
ok "Déploiement terminé."
echo "    Interface      : $(get_env WEBUI_URL)"
echo "    Configuration  : $PROJECT_DIR/.env (secrets : ne jamais le versionner)"
echo "    Étapes suivantes (DEPLOIEMENT_GIT.md §5) :"
echo "      1. créer le compte administrateur dans l'interface, puis ENABLE_SIGNUP=false"
echo "      2. publier les extensions : make deploy-tools KEY=sk-…"
echo "      3. créer le modèle « Assistant Contrats » avec le filtre « RAG forcé »"
echo "      4. déposer les documents dans $INBOX_VAL/<REFERENCE>/ puis : make ingest"
