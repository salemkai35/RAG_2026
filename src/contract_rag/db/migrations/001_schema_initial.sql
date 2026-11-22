-- =============================================================================
-- 001 – Schéma métier initial de la base « contrats »
-- Prérequis (créés par docker/postgres/init) : extensions vector, unaccent, pg_trgm
-- =============================================================================

-- Configuration plein texte française insensible aux accents
CREATE TEXT SEARCH CONFIGURATION fr_unaccent (COPY = french);
ALTER TEXT SEARCH CONFIGURATION fr_unaccent
    ALTER MAPPING FOR hword, hword_part, word WITH unaccent, french_stem;

-- -----------------------------------------------------------------------------
-- Référentiel
-- -----------------------------------------------------------------------------
CREATE TABLE entreprise (
    id                  bigserial PRIMARY KEY,
    nom                 text NOT NULL,
    nom_normalise       text NOT NULL UNIQUE,
    identifiant_fiscal  text,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX entreprise_nom_trgm ON entreprise USING gin (nom_normalise gin_trgm_ops);

CREATE TABLE contrat (
    id                      bigserial PRIMARY KEY,
    reference               text NOT NULL UNIQUE,
    intitule                text,
    objet                   text,
    maitre_ouvrage          text,
    titulaire_id            bigint REFERENCES entreprise(id),
    devise                  text,
    montant_initial_ht      numeric(18,2),
    taux_tva                numeric(6,3),          -- en pourcentage, ex. 19.000
    montant_initial_ttc     numeric(18,2),
    date_signature          date,
    date_notification       date,
    date_ods_demarrage      date,
    delai_execution_jours   integer,
    delai_execution_mois    integer,
    statut                  text,
    source_metadonnees      text NOT NULL DEFAULT 'ingestion'
                            CHECK (source_metadonnees IN ('ingestion','import','extraction','manuel')),
    metadata                jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX contrat_reference_trgm ON contrat USING gin (reference gin_trgm_ops);
CREATE INDEX contrat_libelle_trgm ON contrat
    USING gin ((coalesce(intitule, '') || ' ' || coalesce(objet, '')) gin_trgm_ops);

CREATE TABLE contrat_partie (
    contrat_id      bigint NOT NULL REFERENCES contrat(id) ON DELETE CASCADE,
    entreprise_id   bigint NOT NULL REFERENCES entreprise(id),
    role            text NOT NULL CHECK (role IN
                    ('titulaire','cotraitant','sous_traitant','maitre_ouvrage','maitre_oeuvre','autre')),
    PRIMARY KEY (contrat_id, entreprise_id, role)
);

-- -----------------------------------------------------------------------------
-- Documents
-- -----------------------------------------------------------------------------
CREATE TABLE document (
    id                      bigserial PRIMARY KEY,
    contrat_id              bigint REFERENCES contrat(id) ON DELETE SET NULL,
    sha256                  char(64) NOT NULL UNIQUE,
    nom_fichier             text NOT NULL,
    chemin_source           text,
    chemin_stockage         text NOT NULL,            -- relatif à CRAG_STORAGE_ROOT
    extension               text NOT NULL,
    taille_octets           bigint,
    type_document           text NOT NULL DEFAULT 'autre',
    numero_ordre            integer,                  -- n° d'avenant, d'ODS…
    version                 integer NOT NULL DEFAULT 1,
    remplace_document_id    bigint REFERENCES document(id),
    est_courant             boolean NOT NULL DEFAULT true,
    date_document           date,
    nb_pages                integer,
    statut_traitement       text NOT NULL DEFAULT 'importe'
                            CHECK (statut_traitement IN ('importe','a_vectoriser','indexe','erreur')),
    message_erreur          text,
    avertissements          jsonb NOT NULL DEFAULT '[]'::jsonb,
    metadata                jsonb NOT NULL DEFAULT '{}'::jsonb,
    importe_le              timestamptz NOT NULL DEFAULT now(),
    traite_le               timestamptz
);
CREATE INDEX document_contrat_idx ON document (contrat_id);
CREATE INDEX document_type_idx ON document (type_document);

CREATE TABLE document_page (
    document_id     bigint NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    numero          integer NOT NULL,
    texte           text NOT NULL DEFAULT '',
    methode         text NOT NULL,          -- texte | ocr:tesseract | ocr:vision
    confiance_ocr   real,
    PRIMARY KEY (document_id, numero)
);

CREATE TABLE chunk (
    id              bigserial PRIMARY KEY,
    document_id     bigint NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    contrat_id      bigint REFERENCES contrat(id) ON DELETE SET NULL,
    ordre           integer NOT NULL,
    type_chunk      text NOT NULL CHECK (type_chunk IN ('texte','tableau')),
    page_debut      integer,
    page_fin        integer,
    section_chemin  text,
    section_titre   text,
    table_ref       text,
    cellule_ref     text,
    contenu         text NOT NULL,
    tsv             tsvector GENERATED ALWAYS AS
                    (to_tsvector('fr_unaccent'::regconfig,
                                 coalesce(section_chemin, '') || ' ' || contenu)) STORED,
    embedding       vector({{EMBEDDING_DIM}}),
    embedding_modele text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX chunk_document_idx ON chunk (document_id);
CREATE INDEX chunk_contrat_idx ON chunk (contrat_id);
CREATE INDEX chunk_tsv_idx ON chunk USING gin (tsv);
CREATE INDEX chunk_embedding_idx ON chunk USING hnsw (embedding vector_cosine_ops);

-- -----------------------------------------------------------------------------
-- Événements contractuels (alimentés en phase 4 : extraction + validation)
-- -----------------------------------------------------------------------------
CREATE TABLE avenant (
    id                  bigserial PRIMARY KEY,
    contrat_id          bigint NOT NULL REFERENCES contrat(id) ON DELETE CASCADE,
    numero              integer NOT NULL,
    document_id         bigint REFERENCES document(id) ON DELETE SET NULL,
    date_signature      date,
    objet               text,
    montant_ht_delta    numeric(18,2),
    nouveau_montant_ht  numeric(18,2),
    delai_delta_jours   integer,
    statut_validation   text NOT NULL DEFAULT 'propose'
                        CHECK (statut_validation IN ('propose','valide','corrige','rejete')),
    UNIQUE (contrat_id, numero)
);

CREATE TABLE ordre_service (
    id                  bigserial PRIMARY KEY,
    contrat_id          bigint NOT NULL REFERENCES contrat(id) ON DELETE CASCADE,
    numero              integer,
    document_id         bigint REFERENCES document(id) ON DELETE SET NULL,
    date_os             date,
    type_os             text CHECK (type_os IN ('demarrage','arret','reprise','prolongation','autre')),
    objet               text,
    duree_jours         integer,
    statut_validation   text NOT NULL DEFAULT 'propose'
                        CHECK (statut_validation IN ('propose','valide','corrige','rejete'))
);

CREATE TABLE reception (
    id                  bigserial PRIMARY KEY,
    contrat_id          bigint NOT NULL REFERENCES contrat(id) ON DELETE CASCADE,
    type_reception      text NOT NULL CHECK (type_reception IN ('provisoire','definitive')),
    document_id         bigint REFERENCES document(id) ON DELETE SET NULL,
    date_reception      date,
    avec_reserves       boolean,
    date_levee_reserves date,
    statut_validation   text NOT NULL DEFAULT 'propose'
                        CHECK (statut_validation IN ('propose','valide','corrige','rejete'))
);

-- -----------------------------------------------------------------------------
-- Extraction structurée traçable
-- -----------------------------------------------------------------------------
CREATE TABLE extraction_run (
    id              bigserial PRIMARY KEY,
    demarre_le      timestamptz NOT NULL DEFAULT now(),
    termine_le      timestamptz,
    modele_llm      text,
    version_prompt  text,
    parametres      jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE champ_extrait (
    id                  bigserial PRIMARY KEY,
    contrat_id          bigint NOT NULL REFERENCES contrat(id) ON DELETE CASCADE,
    document_id         bigint REFERENCES document(id) ON DELETE SET NULL,
    chunk_id            bigint REFERENCES chunk(id) ON DELETE SET NULL,
    champ               text NOT NULL,          -- ex. montant_initial_ht, penalite_taux_journalier
    valeur_texte        text,
    valeur_numerique    numeric(18,4),
    valeur_date         date,
    unite               text,
    page                integer,
    section             text,
    cellule_ref         text,
    extrait             text,                   -- citation justificative
    confiance           real,
    source              text NOT NULL CHECK (source IN ('llm','regle','import','manuel')),
    statut              text NOT NULL DEFAULT 'propose'
                        CHECK (statut IN ('propose','valide','corrige','rejete')),
    extraction_run_id   bigint REFERENCES extraction_run(id),
    valide_par          text,
    valide_le           timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX champ_extrait_contrat_idx ON champ_extrait (contrat_id, champ);

-- -----------------------------------------------------------------------------
-- Droits d'accès métier et audit
-- principal : '*' (tous), 'user:<email>', 'group:<nom>' ; contrat_id NULL = tous les contrats
-- -----------------------------------------------------------------------------
CREATE TABLE acces_contrat (
    id          bigserial PRIMARY KEY,
    principal   text NOT NULL,
    contrat_id  bigint REFERENCES contrat(id) ON DELETE CASCADE,
    niveau      text NOT NULL DEFAULT 'lecture' CHECK (niveau IN ('lecture','correction')),
    cree_le     timestamptz NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (principal, contrat_id)
);

CREATE TABLE audit_log (
    id          bigserial PRIMARY KEY,
    horodatage  timestamptz NOT NULL DEFAULT now(),
    acteur      text NOT NULL,
    action      text NOT NULL,
    objet       text NOT NULL,
    objet_id    text,
    avant       jsonb,
    apres       jsonb
);

-- -----------------------------------------------------------------------------
-- Droits du rôle applicatif utilisé par Open WebUI (lecture + corrections tracées)
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'contrats_app') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA public TO contrats_app';
        EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO contrats_app';
        EXECUTE 'GRANT INSERT, UPDATE ON champ_extrait TO contrats_app';
        EXECUTE 'GRANT INSERT ON audit_log TO contrats_app';
        EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO contrats_app';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO contrats_app';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO contrats_app';
    END IF;
END
$$;
