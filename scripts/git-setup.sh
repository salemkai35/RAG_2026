#!/usr/bin/env bash
# =============================================================================
# Prépare le dépôt Git local (à lancer une fois par copie de travail ; idempotent).
#   - active les garde-fous anti-secrets (.githooks/pre-commit et pre-push) ;
#   - impose des fins de ligne Unix et l'exécutabilité des scripts ;
#   - optionnel : relie le dépôt GitHub privé (sans pousser).
#
#   scripts/git-setup.sh
#   scripts/git-setup.sh git@github.com:<organisation>/contrats-rag.git
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

[ -d .git ] || git init -q -b main
git config core.hooksPath .githooks
git config core.autocrlf false
git config core.eol lf
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) git config core.filemode false ;; *) git config core.filemode true ;; esac
git config push.default current
chmod +x .githooks/* deploy.sh scripts/*.sh docker/postgres/init/*.sh 2>/dev/null || true
for f in deploy.sh .githooks/pre-commit .githooks/pre-push scripts/*.sh docker/postgres/init/*.sh; do
  git update-index --chmod=+x "$f" 2>/dev/null || true
done

if ! git config user.name >/dev/null; then
  echo "[!] Identité Git absente pour ce dépôt. Définissez-la (elle apparaîtra sur GitHub) :"
  echo "      git config user.name  \"Prénom Nom\""
  echo "      git config user.email \"identifiant@users.noreply.github.com\""
fi

if [ -n "${1:-}" ]; then
  if git remote get-url origin >/dev/null 2>&1; then git remote set-url origin "$1"; else git remote add origin "$1"; fi
  echo "Dépôt distant : $(git remote get-url origin)  (aucun push effectué)"
fi
echo "Garde-fous actifs : $(git config core.hooksPath)/pre-commit, pre-push"
