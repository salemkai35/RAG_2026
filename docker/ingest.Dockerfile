# Image "ingest" : CLI métier (ingestion, OCR, migrations, contrôles, déploiement des Tools).
# Exécutée à la demande : docker compose run --rm ingest <commande>
FROM python:3.11-slim-bookworm

# true = ajoute LibreOffice pour convertir les anciens formats .doc / .xls (+~400 Mo)
ARG INSTALL_LIBREOFFICE=false

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng \
 && if [ "$INSTALL_LIBREOFFICE" = "true" ]; then \
        apt-get install -y --no-install-recommends libreoffice-writer-nogui libreoffice-calc-nogui; \
    fi \
 && rm -rf /var/lib/apt/lists/*

COPY certs/ /usr/local/share/ca-certificates/contrats-rag/
RUN if ls /usr/local/share/ca-certificates/contrats-rag/*.crt >/dev/null 2>&1; then update-ca-certificates; fi

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[ingest]"
COPY owui_extensions ./owui_extensions

ENV PYTHONUNBUFFERED=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    CRAG_OWUI_EXTENSIONS_DIR=/app/owui_extensions

ENTRYPOINT ["contract-rag"]
CMD ["--help"]
