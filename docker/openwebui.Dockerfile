# Image Open WebUI dérivée : version officielle épinglée + package métier contract_rag.
# Le code d'Open WebUI n'est PAS modifié : on ajoute seulement un package Python
# importable par les Tools / Functions.
ARG OWUI_VERSION=v0.13.4

# --- Étape 1 : construction de la wheel du package métier ---------------------
FROM python:3.11-slim-bookworm AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip wheel --no-deps --no-cache-dir -w /wheels .

# --- Étape 2 : image finale ---------------------------------------------------
FROM ghcr.io/open-webui/open-webui:${OWUI_VERSION}

# Autorités de certification internes (fichiers *.crt déposés dans ./certs).
COPY certs/ /usr/local/share/ca-certificates/contrats-rag/
RUN if ls /usr/local/share/ca-certificates/contrats-rag/*.crt >/dev/null 2>&1 \
       && command -v update-ca-certificates >/dev/null 2>&1; then \
        update-ca-certificates; \
    fi

COPY --from=build /wheels /tmp/wheels
# Les versions des paquets déjà présents dans Open WebUI sont figées (contraintes)
# pour ne jamais modifier ses dépendances ; contract_rag est installé sans deps.
RUN pip freeze | grep -E '^[A-Za-z0-9_.\-]+==' > /tmp/constraints.txt \
 && pip install --no-cache-dir -c /tmp/constraints.txt \
        "psycopg[binary]>=3.1" "psycopg-pool>=3.1" "httpx>=0.25" \
        "openpyxl>=3.1" "python-docx>=1.0" \
 && pip install --no-cache-dir --no-deps /tmp/wheels/*.whl \
 && python -c "import contract_rag, psycopg, psycopg_pool; print('contract_rag', contract_rag.__version__)" \
 && rm -rf /tmp/wheels /tmp/constraints.txt

ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
