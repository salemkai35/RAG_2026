#!/usr/bin/env bash
# =============================================================================
#  Contrats RAG – déploiement en une commande sur un serveur vierge
#  (Rocky Linux / RHEL / Alma 9-10, Ubuntu 22.04-24.04, Debian 12).
#
#    git clone https://github.com/salemkai35/RAG_2026.git && cd RAG_2026
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
#  Serveur Ollama : son adresse par défaut est lue dans config/site.env ; les modèles
#  (LLM, embeddings, OCR) et leurs réglages sont détectés automatiquement.
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
    -h|--help)         awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
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
  ubuntu:*|debian:*|*:*debian*|*:*ubuntu*)                FAMILY=debian ;;
  rocky:*|rhel:*|almalinux:*|centos:*|ol:*|*:*rhel*|*:*fedora*) FAMILY=rhel ;;
  *)                                                       FAMILY=unknown ;;
esac
case "$FAMILY" in
  debian|rhel) ok "Système : ${PRETTY_NAME:-$ID} (famille $FAMILY)" ;;
  *) warn "Système ${PRETTY_NAME:-inconnu} non testé : l'installation des paquets peut échouer." ;;
esac
[ "$(uname -m)" = "x86_64" ] || warn "Architecture $(uname -m) non testée (images prévues pour x86_64)."
mem_gb=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 0)
mem_mb=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)
if [ "${mem_mb:-0}" -ge 7000 ]; then ok "Mémoire : ${mem_gb} Go"
elif [ "${mem_mb:-0}" -ge 3400 ]; then
  warn "Mémoire : $((mem_mb / 1024)) Go — fonctionne pour quelques utilisateurs simultanés (8 Go conseillés)."
else warn "Mémoire : ${mem_mb} Mo — insuffisant (4 Go minimum, 8 Go conseillés)."; fi
free_gb=$(df -Pk "$PROJECT_DIR" | awk 'NR==2 {printf "%d", $4/1024/1024}')
[ "${free_gb:-0}" -ge 15 ] && ok "Disque libre : ${free_gb} Go" || warn "Disque libre : ${free_gb} Go (15 Go minimum)."

# --- 2. Prérequis ------------------------------------------------------------------
log "2/5 Prérequis (Docker, Compose v2, outils)"
apt_install() {
  export DEBIAN_FRONTEND=noninteractive
  $SUDO apt-get update -y
  $SUDO apt-get install -y --no-install-recommends "$@"
}
dnf_install() { $SUDO dnf install -y "$@"; }

install_prereqs_debian() {
  command -v apt-get >/dev/null 2>&1 || die "apt-get absent : installez Docker manuellement puis relancez avec --skip-prereqs."
  [ ${#missing[@]} -eq 0 ] || apt_install ca-certificates "${missing[@]}"
  if ! command -v docker >/dev/null 2>&1; then
    log "Installation de Docker Engine (paquets de la distribution)…"
    apt_install docker.io
  fi
  if ! docker compose version >/dev/null 2>&1; then
    log "Installation du plugin Docker Compose v2…"
    apt_install docker-compose-v2 2>/dev/null || apt_install docker-compose-plugin 2>/dev/null       || die "Compose v2 introuvable dans les dépôts : installez 'docker-compose-plugin' (dépôt Docker officiel ou miroir interne)."
  fi
}

# Rocky / RHEL / Alma : Docker CE vient du dépôt officiel Docker (« rhel »), ou d'un miroir
# interne si DOCKER_REPO_URL est défini. Podman (installé par défaut) entre en conflit avec
# containerd.io : --allowerasing le remplace proprement.
install_prereqs_rhel() {
  command -v dnf >/dev/null 2>&1 || die "dnf absent : installez Docker manuellement puis relancez avec --skip-prereqs."
  [ ${#missing[@]} -eq 0 ] || dnf_install ca-certificates "${missing[@]}"
  if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    log "Installation de Docker CE (dépôt ${DOCKER_REPO_URL:-https://download.docker.com/linux/rhel/docker-ce.repo})…"
    dnf_install dnf-plugins-core
    $SUDO dnf config-manager --add-repo "${DOCKER_REPO_URL:-https://download.docker.com/linux/rhel/docker-ce.repo}"       2>/dev/null || $SUDO dnf config-manager addrepo --from-repofile="${DOCKER_REPO_URL:-https://download.docker.com/linux/rhel/docker-ce.repo}"       || die "Impossible d'ajouter le dépôt Docker (accès Internet ? sinon DOCKER_REPO_URL=<miroir interne>)."
    $SUDO dnf install -y --allowerasing docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin       || die "Installation de Docker CE impossible (voir DEPLOIEMENT_ROCKY_OLLAMA.md §3.2)."
  fi
}

if [ "$SKIP_PREREQS" -eq 0 ]; then
  missing=()
  for tool in git curl openssl make tar python3; do command -v "$tool" >/dev/null 2>&1 || missing+=("$tool"); done
  case "$FAMILY" in
    rhel) install_prereqs_rhel ;;
    *)    install_prereqs_debian ;;
  esac
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

DETECTED=0
if [ "$NEW_ENV" -eq 1 ] || [ "$INTERACTIVE" -eq 1 ]; then
  # --- Serveur Ollama : un seul serveur pour tous les modèles, détectés automatiquement ---
  # Adresse par défaut : config/site.env (valeur du site, versionnée, non secrète).
  if [ -z "${OLLAMA_URL:-}" ] && [ -z "$(get_env OLLAMA_URL)" ] && [ -f config/site.env ]; then
    site_ollama="$(awk -F= '$1 == "OLLAMA_URL" { sub(/^[^=]*=/, ""); print; exit }' config/site.env)"
    [ -n "$site_ollama" ] && set_env OLLAMA_URL "$site_ollama"
  fi
  [ "$INTERACTIVE" -eq 1 ] && echo "    Entrée = garder la valeur proposée."
  ask OLLAMA_URL "Serveur Ollama (vide = autre API compatible OpenAI)"
  OLLAMA="$(get_env OLLAMA_URL)"; OLLAMA="${OLLAMA%/}"; OLLAMA="${OLLAMA%/v1}"
  if [ -n "$OLLAMA" ] && [ "$NEW_ENV" -eq 1 ]; then
    log "Détection des modèles sur $OLLAMA…"
    if det="$(python3 scripts/ollama-detect.py "$OLLAMA" 2>&1)"; then
      dval() { printf '%s\n' "$det" | awk -F= -v k="$1" '$1 == k { sub(/^[^=]*=/, ""); print; exit }'; }
      # Valeur détectée, sauf si l'exploitant l'a fixée dans l'environnement.
      put() { [ -n "${!1:-}" ] || set_env "$1" "$2"; }
      for k in LLM_MODEL EMBEDDING_MODEL EMBEDDING_DIM EMBEDDING_REQUEST_DIMENSIONS EMBEDDING_TRUNCATE \
               EMBEDDING_QUERY_PREFIX EMBEDDING_DOCUMENT_PREFIX OWUI_BYPASS_EMBEDDING; do
        printf '%s\n' "$det" | grep -q "^$k=" && put "$k" "$(dval "$k")"
      done
      put LLM_BASE_URL "$OLLAMA/v1"
      put EMBEDDING_BASE_URL "$OLLAMA/v1"
      put RERANKER_API none
      if [ -n "$(dval VISION_MODEL)" ]; then
        put OCR_ENGINE vision
        put VISION_BASE_URL "$OLLAMA/v1"
        put VISION_MODEL "$(dval VISION_MODEL)"
        put COMPOSE_PROFILES ocr                     # passerelle OCR pour les scans du chat
        put OWUI_CONTENT_EXTRACTION_ENGINE external
      fi
      ok "Ollama $(dval OLLAMA_VERSION) : $(printf '%s' "$(dval OLLAMA_MODELS)" | tr ',' ' ')"
      echo "     LLM        : $(get_env LLM_MODEL)"
      echo "     Embeddings : $(get_env EMBEDDING_MODEL) (dimension native $(dval EMBEDDING_NATIVE_DIM) → $(get_env EMBEDDING_DIM))"
      echo "     OCR        : $( [ "$(get_env OCR_ENGINE)" = vision ] && get_env VISION_MODEL || echo "tesseract (aucun modèle OCR trouvé)")"
      [ -n "$(dval AVERTISSEMENT)" ] && warn "$(dval AVERTISSEMENT)"
      [ -n "$(get_env LLM_MODEL)" ] && [ -n "$(get_env EMBEDDING_MODEL)" ] && DETECTED=1
      if [ "$DETECTED" -eq 1 ] && [ "$INTERACTIVE" -eq 1 ]; then
        read -r -p "    Utiliser cette configuration ? [O/n] " answer < /dev/tty || answer=""
        case "${answer:-o}" in [nN]*) DETECTED=0 ;; esac
      fi
    else
      warn "Détection impossible : ${det:-Ollama injoignable}. Saisie manuelle."
    fi
  fi
fi

if { [ "$NEW_ENV" -eq 1 ] || [ "$INTERACTIVE" -eq 1 ]; } && [ "$DETECTED" -eq 0 ]; then
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
  ask OCR_ENGINE         "OCR des scans : tesseract (local) | vision (modèle) | none"
  if [ "$(get_env OCR_ENGINE)" = "vision" ]; then
    [ -n "$(get_env VISION_BASE_URL)" ] || set_env VISION_BASE_URL "$(get_env LLM_BASE_URL)"
    ask VISION_BASE_URL  "URL du modèle Vision / OCR"
    ask VISION_MODEL     "Nom du modèle Vision / OCR"
  fi
  if [ "$(get_env RERANKER_API)" != "none" ]; then
    ask RERANKER_BASE_URL "URL du reranker"
    ask RERANKER_MODEL    "Nom du modèle de reranking"
  fi
fi

if [ "$NEW_ENV" -eq 1 ] || [ "$INTERACTIVE" -eq 1 ]; then
  # Liste blanche par défaut : les hôtes des URL saisies ou détectées.
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
for v in COMPOSE_PROJECT_NAME COMPOSE_PROFILES APP_VERSION OWUI_BIND INBOX_DIR TZ OCR_ENGINE OCR_LANGUAGES \
         EMBEDDING_QUERY_PREFIX EMBEDDING_DOCUMENT_PREFIX EMBEDDING_REQUEST_DIMENSIONS EMBEDDING_TRUNCATE \
         DEFAULT_CONTRACT_ACCESS ENABLE_SIGNUP VISION_BASE_URL VISION_MODEL VISION_API_KEY VISION_MAX_SIDE \
         OWUI_CONTENT_EXTRACTION_ENGINE OWUI_BYPASS_EMBEDDING; do
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
# SELinux (Rocky/RHEL, mode Enforcing) : les dossiers montés dans les conteneurs doivent
# porter le type container_file_t, sinon PostgreSQL et Open WebUI ne peuvent pas y écrire.
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce 2>/dev/null)" = "Enforcing" ]; then
  for d in "$DATA_ROOT_VAL" "$INBOX_VAL" exports backups docker/postgres/init certs; do
    [ -e "$d" ] || continue
    if command -v semanage >/dev/null 2>&1; then
      abs="$(cd "$d" && pwd)"
      $SUDO semanage fcontext -a -t container_file_t "${abs}(/.*)?" 2>/dev/null         || $SUDO semanage fcontext -m -t container_file_t "${abs}(/.*)?" 2>/dev/null || true
      $SUDO restorecon -R "$abs" >/dev/null 2>&1 || true
    else
      $SUDO chcon -R -t container_file_t "$d" 2>/dev/null || true
    fi
  done
  ok "SELinux Enforcing : dossiers montés étiquetés container_file_t"
fi
# Pare-feu (firewalld) : ouverture du port de l'interface uniquement.
if command -v firewall-cmd >/dev/null 2>&1 && $SUDO firewall-cmd --state >/dev/null 2>&1; then
  port="$(get_env OWUI_PORT)"; port="${port:-3000}"
  if ! $SUDO firewall-cmd --query-port="${port}/tcp" >/dev/null 2>&1; then
    $SUDO firewall-cmd --permanent --add-port="${port}/tcp" >/dev/null && $SUDO firewall-cmd --reload >/dev/null       && ok "firewalld : port ${port}/tcp ouvert"
  else
    ok "firewalld : port ${port}/tcp déjà ouvert"
  fi
fi
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
echo "    Étapes suivantes (DEPLOIEMENT_GIT.md §4.5) :"
echo "      1. créer le compte administrateur dans l'interface, puis ENABLE_SIGNUP=false"
echo "      2. publier les extensions : make deploy-tools KEY=sk-…"
echo "      3. créer le modèle « Assistant Contrats » avec le filtre « RAG forcé »"
echo "      4. déposer les documents dans $INBOX_VAL/<REFERENCE>/ puis : make ingest"
