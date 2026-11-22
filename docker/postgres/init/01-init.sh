#!/bin/bash
# Exécuté UNE SEULE FOIS par l'image PostgreSQL, à la création du volume de données.
# Crée deux bases strictement séparées :
#   openwebui : données internes d'Open WebUI + vecteurs de son RAG natif
#   contrats  : données métier (référentiel, chunks, vecteurs, extractions, audit)
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
     -v owui_pw="$OWUI_DB_PASSWORD" \
     -v ingest_pw="$CONTRATS_INGEST_PASSWORD" \
     -v app_pw="$CONTRATS_APP_PASSWORD" <<'EOSQL'
CREATE ROLE owui LOGIN PASSWORD :'owui_pw';
CREATE DATABASE openwebui OWNER owui;

-- Propriétaire du schéma métier : migrations + ingestion
CREATE ROLE contrats_ingest LOGIN PASSWORD :'ingest_pw';
-- Rôle utilisé par les Tools dans Open WebUI : lecture + corrections tracées
CREATE ROLE contrats_app LOGIN PASSWORD :'app_pw';
CREATE DATABASE contrats OWNER contrats_ingest;

REVOKE ALL ON DATABASE openwebui FROM PUBLIC;
REVOKE ALL ON DATABASE contrats FROM PUBLIC;
GRANT CONNECT ON DATABASE contrats TO contrats_app;
EOSQL

# Extensions (nécessitent le superutilisateur)
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname openwebui <<'EOSQL'
CREATE EXTENSION IF NOT EXISTS vector;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname contrats <<'EOSQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
EOSQL

echo "Bases openwebui et contrats initialisées."
