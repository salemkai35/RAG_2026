# Architecture du projet Contrats RAG – et comment le déployer ailleurs

Ce document décrit **toute l'architecture** de la solution : les composants, les flux, les
données, le code, la sécurité. Il explique ensuite **comment la déployer facilement sur un
autre serveur**.

Le pas-à-pas détaillé, la référence complète de `.env` et le dépannage sont dans
[GUIDE_DEPLOIEMENT.md](GUIDE_DEPLOIEMENT.md).

---

## Sommaire
1. [Objectif et principes](#1-objectif-et-principes)
2. [Vue d'ensemble](#2-vue-densemble)
3. [Les composants](#3-les-composants)
4. [Les flux de traitement](#4-les-flux-de-traitement)
5. [Les données et leur stockage](#5-les-données-et-leur-stockage)
6. [Organisation du code](#6-organisation-du-code)
7. [Sécurité et confidentialité](#7-sécurité-et-confidentialité)
8. [Fiabilité des réponses (anti-hallucination)](#8-fiabilité-des-réponses-anti-hallucination)
9. [Ce qui rend le projet portable](#9-ce-qui-rend-le-projet-portable)
10. [Déployer ailleurs en 3 scénarios](#10-déployer-ailleurs-en-3-scénarios)
11. [Évolutions prévues](#11-évolutions-prévues)

---

## 1. Objectif et principes

L'outil permet d'exploiter environ 200 contrats de travaux (PDF, Word, Excel, y compris les
scans) en langage naturel, en français. Il sert à rechercher, consulter, comparer et
synthétiser les contrats, **avec la source de chaque information** : document, page, article,
cellule.

Principes retenus :

| Principe | Traduction concrète |
|---|---|
| Open WebUI au centre | Interface, comptes, conversations, accès aux modèles. Version **épinglée, jamais modifiée**. |
| Logique métier séparée | Package Python `contract_rag`, indépendant d'Open WebUI, testable seul. |
| Minimum de composants | Pas de FastAPI, Redis, Celery ni Elasticsearch : PostgreSQL fait le relationnel, le vectoriel et le plein texte. |
| Aucune IA externe | Uniquement des API internes au format OpenAI, avec une liste blanche d'hôtes. |
| Traçabilité | Chaque passage garde son document, sa page, sa section, son tableau ou ses cellules. |
| Portabilité | Un seul fichier de configuration (`.env`), tout l'état dans un seul dossier (`data/`), un paquet hors ligne. |

---

## 2. Vue d'ensemble

```
                       ┌────────────────────── Serveur Linux (Docker) ──────────────────────┐
                       │                                                                    │
  Utilisateurs ───────►│  ┌──────────────────────────────────────────────┐                  │
  navigateur (:3000)   │  │ open-webui  (image dérivée v0.13.4)          │                  │
  LDAP / OIDC          │  │  • interface, comptes, groupes, conversations│                  │
                       │  │  • Function filtre « RAG forcé »  ───────┐   │                  │
                       │  │  • Tool « Recherche et consultation » ──┤   │                  │
                       │  │  • package contract_rag  ◄──────────────┘   │                  │
                       │  └──────────────┬───────────────────┬──────────┘                  │
                       │                 │ SQL               │ HTTP (format OpenAI)        │
                       │  ┌──────────────▼──────────────┐    │                             │
                       │  │ postgres (PG 17 + pgvector) │    │                             │
                       │  │  base openwebui  (interne)  │    │                             │
                       │  │  base contrats   (métier)   │◄───┼──────┐                      │
                       │  └─────────────────────────────┘    │      │ SQL                  │
                       │                                     │  ┌───┴──────────────────┐   │
                       │                                     │  │ ingest (à la demande)│   │
                       │   inbox/ (dépôt) ─────────────────────►│ lecture, OCR, chunks │   │
                       │                                     │  │ embeddings, CLI      │   │
                       │   data/ (état persistant)           │  └───┬──────────────────┘   │
                       └─────────────────────────────────────┼──────┼──────────────────────┘
                                                             ▼      ▼
                                    ┌─────────────── Réseau interne de l'entreprise ─────┐
                                    │  API LLM · API Embeddings · API Reranker · OCR/Vision│
                                    └──────────────────────────────────────────────────────┘
```

Il y a **trois conteneurs** applicatifs (`compose.yml`) :
- `open-webui` et `postgres` tournent en permanence ;
- `ingest` est lancé à la demande.

Les **modèles d'IA sont à l'extérieur**, sur les API internes.

---

## 3. Les composants

### 3.1 `open-webui` : l'interface et l'orchestration
- **Image** : `ghcr.io/open-webui/open-webui:v0.13.4` **plus** le package `contract_rag`
  installé par pip ([docker/openwebui.Dockerfile](docker/openwebui.Dockerfile)). Le code
  d'Open WebUI n'est pas modifié. Les versions de ses dépendances sont figées pendant
  l'installation.
- **Rôle** :
  - interface web ;
  - authentification (comptes locaux, LDAP, OIDC) ;
  - groupes et droits sur les modèles et les outils ;
  - historique des conversations ;
  - connexion au LLM interne ;
  - RAG natif pour les documents déposés ponctuellement dans une conversation.
- **Extensions** (code minimal, toute la logique est dans le package) :
  - **Function filtre** `contrats_contexte` : avant chaque réponse, elle recherche les passages
    pertinents et les injecte avec leurs sources. Après la réponse, elle signale les montants
    absents des sources ;
  - **Tool** `contrats_recherche` : recherche filtrée, liste des contrats, fiche contrat. Le LLM
    peut l'appeler s'il sait appeler des outils.
- **Configuration** : uniquement par variables d'environnement issues de `.env`. Base sur
  PostgreSQL, mode hors ligne, pas de télémétrie, aucune installation pip à l'exécution.

### 3.2 `postgres` : toute la persistance
- **Image** : `pgvector/pgvector:0.8.0-pg17`.
- **Deux bases séparées**, créées au premier démarrage par
  [docker/postgres/init/01-init.sh](docker/postgres/init/01-init.sh) :

| Base | Contenu | Propriétaire |
|---|---|---|
| `openwebui` | Tables internes d'Open WebUI : comptes, conversations, extensions, vecteurs du RAG natif | rôle `owui` |
| `contrats` | Référentiel métier, documents, pages, chunks, vecteurs, extractions, droits, audit | rôle `contrats_ingest` |

- **Trois rôles**, chacun avec le minimum nécessaire :
  - `owui` : Open WebUI ;
  - `contrats_ingest` : ingestion et migrations ;
  - `contrats_app` : les Tools, en lecture, plus les corrections tracées.
- **Non exposé** sur le réseau : seuls les conteneurs y accèdent.

### 3.3 `ingest` : les traitements lourds, hors d'Open WebUI
- **Image** : Python 3.11, Tesseract OCR (français et anglais), le package `contract_rag` avec
  ses dépendances d'ingestion. LibreOffice est optionnel, pour les `.doc` et `.xls`.
- **Lancement à la demande** : `docker compose run --rm ingest <commande>`. Le conteneur s'arrête
  à la fin.
- **Commandes** (`contract-rag`) :

| Commande | Rôle |
|---|---|
| `db upgrade` | migrations du schéma métier |
| `check` | contrôle de la base, du stockage, du LLM, des embeddings, du reranker et de l'OCR |
| `ingest /inbox` | ingestion des documents |
| `reindex` | vectorisation des chunks en attente (`--all` après un changement de modèle) |
| `contracts import` | import du référentiel CSV |
| `search`, `show`, `documents` | tests et consultation |
| `access` | droits par utilisateur ou groupe |
| `owui deploy` | publication des extensions dans Open WebUI |

### 3.4 Les API internes de modèles (hors projet)
Elles sont toutes appelées au **format OpenAI** et configurées dans `.env` :

| Rôle | Appel | Exemple de serveur interne |
|---|---|---|
| LLM | `POST /v1/chat/completions` | vLLM, Ollama, passerelle |
| Embeddings | `POST /v1/embeddings` | TEI, Infinity, vLLM |
| Reranker | `POST /rerank` (formats Cohere ou TEI) | TEI, Infinity, vLLM |
| OCR vision (optionnel) | `POST /v1/chat/completions` avec image | modèle vision interne |

---

## 4. Les flux de traitement

### 4.1 Ingestion d'un document

```
inbox/<REF>/fichier ─► empreinte SHA-256 ─► déjà indexé ? ── oui ─► ignoré
                                              │ non
                                              ▼
                     copie immuable : data/contrats/originals/ab/<sha>.pdf
                                              │
          ┌───────────────────────────────────┼──────────────────────────────┐
          ▼                                   ▼                              ▼
   PDF (pdfplumber)                      Word (python-docx)          Excel (openpyxl)
   texte + tableaux par page            titres, paragraphes,         feuilles, références
   page sans texte ─► OCR               tableaux, dans l'ordre       de cellules exactes
   (Tesseract ou Vision)
          └───────────────────────────────────┼──────────────────────────────┘
                                              ▼
              structuration : CHAPITRE > Article 12 – Pénalités (titres détectés)
              découpage : 1 chunk = 1 article (ou une partie), tableaux à part
                                              ▼
              embeddings (API interne) + index plein texte français
              en cas d'échec des embeddings ─► statut « a_vectoriser » (reprise par reindex)
                                              ▼
                  base contrats : document, document_page, chunk (vecteur + tsvector)
```

- Le **type** du document (contrat, avenant n°, ordre de service, PV de réception, caution,
  BPU…) est déduit du nom de fichier, ou fixé dans un `manifest.csv`.
- Le **sous-dossier** de `inbox/` donne la **référence du contrat** : c'est ce qui rattache les
  pièces à leur contrat.
- Une **nouvelle version** d'un document se déclare dans le manifeste (colonne `remplace`).
  L'ancienne version est conservée, marquée comme remplacée.

### 4.2 Question d'un utilisateur

```
Question ─► Open WebUI ─► Filter « RAG forcé » (inlet)
                             │
                             ▼  contract_rag : droits de l'utilisateur (acces_contrat)
              ┌──── recherche hybride, en UNE requête SQL dans la base contrats ────┐
              │  plein texte français (racinisation, sans accents)                  │
              │  + similarité vectorielle (pgvector, cosinus)                       │
              │  fusion des deux classements (Reciprocal Rank Fusion)               │
              │  + filtres : contrat, type de document, titulaire, dates, montants  │
              └──────────────────────────────┬──────────────────────────────────────┘
                                             ▼
                        reranker interne (optionnel) ─► passages les plus pertinents
                                             ▼
              injection des passages sourcés [1]…[n] + citations affichées dans l'interface
                                             ▼
                                    LLM interne : rédaction
                                             ▼
              Filter (outlet) : montants ou dates absents des sources ─► « ⚠️ à vérifier »
                                             ▼
                                 Réponse avec sources cliquables
```

Si un service est indisponible, la recherche continue en mode dégradé, avec un avertissement :
- **embeddings indisponibles** : recherche par mots-clés seuls ;
- **reranker indisponible** : classement hybride sans reranking.

### 4.3 Fiche contrat (Tool)
La fiche réunit le référentiel (montants, dates, délais, titulaire), les documents et leurs
versions, les avenants et l'**évolution du montant calculée en Python**. S'y ajoutent des
**contrôles automatiques** :
- incohérence HT/TVA/TTC ;
- avenant manquant dans la numérotation ;
- document en erreur ;
- information non renseignée.

---

## 5. Les données et leur stockage

### 5.1 Où est l'état ?
**Tout** l'état est sous `DATA_ROOT`, par défaut `./data` :

```
data/
├── postgres/        les 2 bases (openwebui + contrats)
├── open-webui/      fichiers internes d'Open WebUI (téléversements, caches)
└── contrats/
    └── originals/   documents originaux, immuables, nommés par empreinte SHA-256
```

Les conteneurs, eux, ne contiennent **aucune donnée** : on peut les supprimer et les recréer
sans rien perdre.

### 5.2 Schéma de la base `contrats`
Défini dans [src/contract_rag/db/migrations/001_schema_initial.sql](src/contract_rag/db/migrations/001_schema_initial.sql) :

```
entreprise ◄── contrat ──► contrat_partie
                  │
                  ├──► document ──► document_page   (texte par page, méthode texte ou OCR)
                  │        │
                  │        └──► chunk               (contenu, page, section, tableau, cellules,
                  │                                  vecteur, index plein texte)
                  ├──► avenant · ordre_service · reception     (événements contractuels)
                  ├──► champ_extrait   (valeur + extrait justificatif + page + statut
                  │                     proposé / validé / corrigé)
                  └──► acces_contrat   (droits par utilisateur ou groupe)
audit_log · extraction_run · schema_migrations
```

### 5.3 Traçabilité
Chaque réponse remonte jusqu'au fichier d'origine :
`réponse → citation [n] → chunk → document + page / section / cellule → fichier original (SHA-256)`.

---

## 6. Organisation du code

```
.
├── compose.yml                 les 3 services Docker
├── .env.example                modèle de configuration unique
├── Makefile                    raccourcis (make help)
├── ARCHITECTURE.md             ce document
├── GUIDE_DEPLOIEMENT.md        déploiement pas à pas + compte rendu de test
├── docker/
│   ├── openwebui.Dockerfile    Open WebUI épinglé + contract_rag
│   ├── ingest.Dockerfile       Python + Tesseract (+ LibreOffice optionnel)
│   └── postgres/init/          création des 2 bases et 3 rôles
├── scripts/
│   ├── install.sh              installation / mise à jour (connectée ou hors ligne)
│   ├── bundle-offline.sh       paquet autonome pour un serveur sans Internet
│   ├── backup.sh               sauvegarde complète
│   └── restore.sh              restauration
├── src/contract_rag/           LOGIQUE MÉTIER (indépendante d'Open WebUI)
│   ├── config/settings.py      toute la configuration (variables CRAG_*)
│   ├── gateways/               clients des API modèles : llm, embeddings, reranker, ocr
│   ├── db/                     connexion, migrations SQL versionnées
│   ├── domain/                 calculs purs : montants, TVA, avenants, délais, pénalités
│   ├── ingestion/              lecteurs PDF/Word/Excel, structure, découpage, pipeline
│   ├── retrieval/hybrid.py     recherche hybride SQL
│   ├── repositories/           accès SQL
│   ├── services/               recherche, fiche contrat, contrôles
│   ├── security/authz.py       périmètre d'accès par utilisateur ou groupe
│   ├── presentation.py         mise en forme, citations, garde-fou des montants
│   ├── owui/                   SEUL module lié à Open WebUI (adaptateurs, déploiement)
│   └── cli.py                  commande contract-rag
├── owui_extensions/            ce qui est publié dans Open WebUI
│   ├── tools/contrats_recherche.py
│   ├── functions/contrats_contexte.py
│   └── prompts/assistant_contrats_system.md
├── tests/                      72 tests unitaires (sans base ni modèle)
├── docs/                       connexion des modèles, import CSV, points à vérifier
├── eval/                       questions de référence pour évaluer la qualité
├── inbox/                      dépôt des documents (un sous-dossier par contrat)
└── certs/                      autorités de certification internes (*.crt)
```

**Règle de dépendances** : les flèches vont toujours dans ce sens.

```
owui_extensions ─► contract_rag.owui ─► services ─► domain / repositories / gateways
```

Le package ne dépend donc pas d'Open WebUI. Une future API (FastAPI, ou un serveur d'outils
OpenAPI/MCP, pris en charge nativement par Open WebUI) pourra réutiliser `services/` sans
réécriture.

---

## 7. Sécurité et confidentialité

| Risque | Mesure |
|---|---|
| Envoi de données vers une IA publique | Uniquement les API configurées dans `.env`. **Liste blanche** `AI_ALLOWED_HOSTS` : tout autre hôte est refusé avant l'appel (testé avec api.openai.com). |
| Fuite par un proxy | Les variables `HTTP(S)_PROXY` sont ignorées par le package. |
| Télémétrie, mises à jour automatiques | `OFFLINE_MODE`, vérification de version désactivée, télémétrie désactivée, pas d'installation pip à l'exécution. |
| Accès réseau à la base | PostgreSQL n'est pas publié, et chaque rôle n'a que les droits nécessaires. |
| Accès aux contrats | Les droits Open WebUI s'appliquent aux modèles et aux outils, et des **droits métier** (`acces_contrat`) s'appliquent par contrat, utilisateur ou groupe. |
| HTTPS des API internes | Les CA internes (`certs/`) sont intégrées aux images, et la vérification TLS est active. |
| Secrets | Générés aléatoirement à l'installation, dans `.env` (droits 600), jamais dans le code. |
| Sortie réseau | Recommandé en production : pare-feu (`DOCKER-USER`) n'autorisant que les API internes ([docs/POINTS_A_VERIFIER.md](docs/POINTS_A_VERIFIER.md)). |
| Traçabilité des modifications | Table `audit_log` : imports, droits, et plus tard les corrections. |

---

## 8. Fiabilité des réponses (anti-hallucination)

Les tests ont montré qu'**on ne peut pas compter sur le LLM pour appeler les outils** : un modèle
7B les simulait et inventait les chiffres. L'architecture ne dépend donc plus de lui pour la
recherche.

1. **Recherche forcée** (Function filtre) : les passages sourcés sont *toujours* fournis au LLM,
   avec la consigne de ne répondre qu'à partir d'eux.
2. **Consigne « information non trouvée »** : testé, une question hors corpus n'obtient pas de
   réponse inventée.
3. **Garde-fou de sortie** : tout montant, date ou quantité de la réponse absent des sources est
   signalé « ⚠️ à vérifier ».
4. **Calculs en Python, jamais par le LLM** (`domain/`) : TTC, évolution par avenant, délais,
   pénalités.
5. **Citations** affichées dans l'interface, pour une vérification en un clic.

Limite connue : un chiffre présent dans les sources mais mal utilisé n'est pas détecté. Pour la
production, prévoir un LLM interne plus capable qu'un 7B, et les Tools de calcul de la phase 4.

---

## 9. Ce qui rend le projet portable

| Élément | Pourquoi c'est portable |
|---|---|
| **Docker + Compose** | Le même `compose.yml` fonctionne sur toute distribution Linux x86_64 avec Docker 24+. |
| **Un seul `.env`** | Endpoints, secrets, ports, chemins, version : rien d'autre à modifier. |
| **Tout l'état dans `data/`** | Sauvegarder ou déplacer ce dossier (ou un dump) suffit. |
| **Versions épinglées** | Open WebUI v0.13.4, pgvector 0.8.0 et PG17, Python 3.11 : comportement identique partout. |
| **Paquet hors ligne** | Les images Docker sont incluses : installation sans Internet. |
| **Scripts idempotents** | `install.sh` sert à l'installation **et** à la mise à jour ; les migrations ne s'appliquent qu'une fois. |
| **Modèles découplés** | On change d'API ou de modèle en modifiant 3 variables, sans toucher au code. |
| **Nouvelles tentatives réseau** | Les incidents réseau passagers (DNS, coupure) n'interrompent pas l'installation. |

---

## 10. Déployer ailleurs en 3 scénarios

Prérequis communs sur le serveur cible : Linux x86_64, **8 Go de RAM minimum**, Docker et Compose v2.
```bash
# Ubuntu / Debian
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 make
sudo systemctl enable --now docker
```

### Scénario A : le serveur cible a accès à Internet ou aux registres internes
```powershell
# 1. Sur le poste Windows, dans le dossier du projet : créer l'archive
tar -czf contrats-rag.tar.gz --exclude=.venv --exclude=data --exclude=exports --exclude=backups --exclude=dist --exclude=.env .
scp contrats-rag.tar.gz utilisateur@serveur:/tmp/
```
```bash
# 2. Sur le serveur
sudo mkdir -p /opt/contrats-rag && sudo tar -xzf /tmp/contrats-rag.tar.gz -C /opt/contrats-rag
cd /opt/contrats-rag && sudo chmod +x scripts/*.sh docker/postgres/init/*.sh
sudo ./scripts/install.sh      # 1er passage : crée .env et génère les secrets
sudo nano .env                 # renseigner les API internes (LLM, embeddings, reranker)
sudo ./scripts/install.sh      # 2e passage : construit, démarre, migre, contrôle
```

### Scénario B : le serveur cible n'a PAS Internet (recommandé en environnement sécurisé)
```bash
# 1. Sur une machine connectée avec Docker (ou WSL) : produire le paquet (≈ 1,8 Go)
./scripts/bundle-offline.sh            # -> dist/contrats-rag-0.1.0.tar.gz + .sha256
```
```bash
# 2. Transférer les 2 fichiers, puis sur le serveur isolé
sha256sum -c contrats-rag-0.1.0.tar.gz.sha256
sudo tar -xzf contrats-rag-0.1.0.tar.gz -C /opt && cd /opt/contrats-rag-0.1.0
sudo ./scripts/install.sh   &&   sudo nano .env   &&   sudo ./scripts/install.sh
```
Le script détecte le dossier `images/` et **charge** les images au lieu de les construire.
Aucun téléchargement n'est nécessaire.

### Scénario C : déménager une installation existante avec ses données
```bash
# Ancien serveur
./scripts/backup.sh /srv/sauvegardes      # bases + fichiers + .env (+ empreintes)
# Nouveau serveur : copier le projet (A ou B), puis
cp /chemin/sauvegarde/env.backup .env     # mêmes secrets
./scripts/install.sh
./scripts/restore.sh /chemin/sauvegarde/AAAAMMJJ-HHMMSS
```

### Après le déploiement (dans tous les cas)
1. Ouvrir `http://<serveur>:3000` et créer le **premier compte**, qui est administrateur.
   Passer ensuite `ENABLE_SIGNUP=false`.
2. Publier les extensions :
   `docker compose run --rm ingest owui deploy --api-key <clé API Open WebUI>`.
3. Créer le modèle « Assistant Contrats » : LLM interne, prompt système, **filtre RAG forcé**,
   Tool.
4. Déposer les documents dans `inbox/<REFERENCE>/`, puis `make ingest`.
5. Définir les droits : `docker compose run --rm ingest access grant "group:<Groupe>"`.
6. Planifier `scripts/backup.sh` (cron quotidien).

Le détail de chaque étape, la configuration LDAP/OIDC et HTTPS, et le dépannage sont dans
[GUIDE_DEPLOIEMENT.md](GUIDE_DEPLOIEMENT.md).

### Validé en conditions réelles
Les scénarios A, B et C ont été exécutés le 21/11/2026 sur Ubuntu 24.04 (WSL2) avec Docker 29.
Le compte rendu (18 tests) est dans [GUIDE_DEPLOIEMENT.md §12](GUIDE_DEPLOIEMENT.md).

---

## 11. Évolutions prévues

| Phase | Contenu | Impact sur l'architecture |
|---|---|---|
| 4 | Extraction structurée par LLM des champs du contrat (avec extrait justificatif), validation par aller-retour Excel, Tools de comparaison et d'analyse des avenants, exports Excel et Word | Aucun nouveau composant : de nouveaux modules dans `contract_rag` et de nouvelles commandes `ingest` |
| Plus tard, si besoin | Interface de validation multi-utilisateurs, autre application consommatrice, import asynchrone depuis l'interface | Ajout d'une API (FastAPI ou serveur d'outils OpenAPI/MCP) qui **réutilise `services/`** ; Redis et un worker seulement si les volumes le justifient |
