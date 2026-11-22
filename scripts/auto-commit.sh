#!/usr/bin/env bash
# =============================================================================
# Commit automatique LOCAL des modifications significatives. Ne fait JAMAIS de push.
#
#   scripts/auto-commit.sh                 message descriptif généré depuis les changements
#   scripts/auto-commit.sh "Mon message"   message fourni (1re ligne = titre)
#   make commit  /  make commit M="…"      mêmes usages
#   scripts/auto-commit.sh --hook          appelé par le hook Claude Code (fin de chaque tour)
#
# Règles :
#   - respecte .gitignore et le garde-fou .githooks/pre-commit (secrets, données) ;
#   - ignore les modifications non significatives (espaces / fins de ligne uniquement) ;
#   - n'envoie rien : la publication reste « git push », à votre initiative.
# =============================================================================
set -uo pipefail
cd "$(dirname "$0")/.." || exit 0

HOOK=0
MESSAGE=""
if [ "${1:-}" = "--hook" ]; then
  HOOK=1
  input="$(cat 2>/dev/null || true)"
  # Évite une boucle si un premier blocage a déjà relancé l'agent.
  printf '%s' "$input" | grep -Eq '"stop_hook_active"[[:space:]]*:[[:space:]]*true' && exit 0
else
  MESSAGE="${1:-}"
fi

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
[ -z "$(git status --porcelain)" ] && { [ "$HOOK" -eq 1 ] || echo "Rien à valider."; exit 0; }

git add -A

# Modifications purement cosmétiques (espaces, CRLF) : on n'en fait pas un commit.
if git diff --cached --quiet --ignore-all-space --ignore-cr-at-eol \
   && [ -z "$(git diff --cached --name-only --diff-filter=ADR)" ]; then
  git reset -q
  [ "$HOOK" -eq 1 ] || echo "Changements non significatifs (espaces) : pas de commit."
  exit 0
fi

# --- Message descriptif généré ----------------------------------------------------
if [ -z "$MESSAGE" ]; then
  mapfile -t lines < <(git diff --cached --name-status -M)
  declare -A areas=()
  added=0; modified=0; deleted=0
  for l in "${lines[@]}"; do
    st="${l%%$'\t'*}"; path="${l##*$'\t'}"
    case "$st" in A*) added=$((added+1)) ;; D*) deleted=$((deleted+1)) ;; *) modified=$((modified+1)) ;; esac
    case "$path" in
      src/contract_rag/*/*) a="${path#src/contract_rag/}"; a="code ${a%%/*}" ;;
      src/contract_rag/*)   a="code $(basename "${path%.py}")" ;;
      owui_extensions/*)    a="extensions Open WebUI" ;;
      tests/*)              a="tests" ;;
      docker/*|compose.yml) a="Docker" ;;
      scripts/*|deploy.sh|Makefile) a="scripts" ;;
      .githooks/*|.gitignore|.gitattributes|.dockerignore) a="Git" ;;
      .env.example|pyproject.toml) a="configuration" ;;
      .claude/*)            a="outillage" ;;
      *.md|docs/*)          a="documentation" ;;
      *)                    a="divers" ;;
    esac
    areas["$a"]=1
  done
  scope="$(printf '%s\n' "${!areas[@]}" | sort | paste -sd, - | sed 's/,/, /g')"
  if [ ${#lines[@]} -eq 1 ]; then
    path="${lines[0]##*$'\t'}"
    case "${lines[0]%%$'\t'*}" in
      A*) verb="Ajout de" ;; D*) verb="Suppression de" ;; R*) verb="Renommage vers" ;; *) verb="Mise à jour de" ;;
    esac
    title="$verb $path"
  else
    parts=()
    [ $modified -gt 0 ] && parts+=("$modified modifié(s)")
    [ $added -gt 0 ]    && parts+=("$added ajouté(s)")
    [ $deleted -gt 0 ]  && parts+=("$deleted supprimé(s)")
    title="Mise à jour $scope : $(IFS=,; echo "${parts[*]}" | sed 's/,/, /g')"
  fi
  stat="$(git diff --cached --shortstat | sed 's/^ *//')"
  body="$(printf '%s\n' "${lines[@]}" | awk -F'\t' '{
      s = substr($1,1,1); lbl = (s=="A") ? "ajout" : (s=="D") ? "suppr." : (s=="R") ? "renom." : "modif."
      printf "- %-6s %s\n", lbl, $NF }' | head -40)"
  [ ${#lines[@]} -gt 40 ] && body="$body"$'\n'"- … et $(( ${#lines[@]} - 40 )) autre(s) fichier(s)"
  MESSAGE="$(printf '%s\n\nDomaines : %s\n%s\n\n%s\n\n(commit automatique local – aucun push)' \
             "$title" "$scope" "$stat" "$body")"
fi

# --- Commit (le hook pre-commit contrôle les secrets) ---------------------------------
if out="$(git commit -q -F - <<< "$MESSAGE" 2>&1)"; then
  echo "✔ Commit local : $(git log -1 --format='%h %s')"
  echo "  (rien n'a été poussé : lancez « git push » quand vous le décidez)"
  exit 0
fi

git reset -q
echo "$out" >&2
echo "Commit automatique refusé : les fichiers sont laissés tels quels, non validés." >&2
# En mode hook : code 2 = message renvoyé à l'agent pour qu'il corrige.
[ "$HOOK" -eq 1 ] && exit 2
exit 1
