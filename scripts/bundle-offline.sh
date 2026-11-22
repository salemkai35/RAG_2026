#!/usr/bin/env bash
# =============================================================================
# Produit un paquet d'installation AUTONOME pour un serveur sans Internet.
# À exécuter sur une machine connectée disposant de Docker :
#   ./scripts/bundle-offline.sh
# Résultat : dist/contrats-rag-<version>.tar.gz (+ .sha256)
# Sur le serveur cible : tar xzf … && cd contrats-rag-<version> && ./scripts/install.sh
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE=.env.example
set -a; . "./$ENV_FILE"; set +a
VERSION="${APP_VERSION}"
NAME="contrats-rag-${VERSION}"
OUT="dist/${NAME}"

retry() {  # incidents réseau passagers (DNS, coupure) lors des téléchargements
  for attempt in 1 2 3 4 5; do "$@" && return 0; echo "[!] échec ($attempt/5) : $* — nouvel essai"; sleep 15; done
  return 1
}

echo "==> Construction des images ${VERSION} (Open WebUI ${OWUI_VERSION})"
retry docker compose --env-file "$ENV_FILE" --profile cli build
docker image inspect "${POSTGRES_IMAGE}" >/dev/null 2>&1 || retry docker pull "${POSTGRES_IMAGE}"

rm -rf "$OUT" && mkdir -p "$OUT/images"
echo "==> Export des images"
docker save "contrats-rag/open-webui:${VERSION}" | gzip > "$OUT/images/open-webui.tar.gz"
docker save "contrats-rag/ingest:${VERSION}"     | gzip > "$OUT/images/ingest.tar.gz"
docker save "${POSTGRES_IMAGE}"                  | gzip > "$OUT/images/postgres.tar.gz"

echo "==> Copie des fichiers de déploiement"
cp -r compose.yml .env.example Makefile README.md pyproject.toml deploy.sh *.md docs docker scripts owui_extensions src "$OUT/"
mkdir -p "$OUT/inbox" "$OUT/certs"
cp inbox/README.md "$OUT/inbox/"
cp certs/README.md "$OUT/certs/"
find "$OUT" -name '__pycache__' -type d -prune -exec rm -rf {} +
chmod +x "$OUT"/deploy.sh "$OUT"/scripts/*.sh "$OUT"/docker/postgres/init/*.sh

tar -C dist -czf "dist/${NAME}.tar.gz" "$NAME"
( cd dist && sha256sum "${NAME}.tar.gz" > "${NAME}.tar.gz.sha256" )
rm -rf "$OUT"
echo "==> Paquet prêt : dist/${NAME}.tar.gz ($(du -h "dist/${NAME}.tar.gz" | cut -f1))"
