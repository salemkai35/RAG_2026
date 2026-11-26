# Contrats RAG – IAP / Département Études & Projets

> 🚀 **Déploiement depuis GitHub** : `git clone … && sudo ./deploy.sh` (voir
> [DEPLOIEMENT_GIT.md](DEPLOIEMENT_GIT.md)) ·
> 🖧 **Rocky Linux + serveur Ollama distant (Qwen, OCRia)** :
> [DEPLOIEMENT_ROCKY_OLLAMA.md](DEPLOIEMENT_ROCKY_OLLAMA.md) ·
> 📐 architecture : [ARCHITECTURE.md](ARCHITECTURE.md) ·
> copie par archive, HTTPS, pare-feu : [DEPLOIEMENT_UBUNTU.md](DEPLOIEMENT_UBUNTU.md) ·
> référence de `.env` : [GUIDE_DEPLOIEMENT.md](GUIDE_DEPLOIEMENT.md)

## Démarrage rapide (serveur Rocky Linux, RHEL, Ubuntu ou Debian vierge)

```bash
sudo git clone https://github.com/salemkai35/RAG_2026.git /opt/contrats-rag && cd /opt/contrats-rag
sudo ./deploy.sh
```
`deploy.sh` installe Docker, crée `.env` et ses secrets, interroge le serveur Ollama du site
(`config/site.env` : `http://172.21.36.55:11434`), choisit les modèles (LLM, embeddings, OCR),
puis construit, démarre et contrôle tout. Il ne reste qu'à confirmer d'un « Entrée ».

Solution locale d'exploitation des contrats de travaux : recherche hybride, consultation et
analyse, avec sources vérifiables. Elle est construite sur **Open WebUI** (version épinglée,
non modifiée), **PostgreSQL + pgvector** et un package Python métier, `contract_rag`.
Elle n'utilise aucun service public : tous les modèles sont appelés sur les **API internes**.

```
 Utilisateurs ──► Open WebUI (image dérivée v0.13.4)
                   ├─ Modèle « Assistant Contrats » + Tool contrats_recherche
                   │        └─► package contract_rag (dans l'image)
                   └─ RAG natif (documents ponctuels)
                          │                         │
              ┌───────────▼─────────────────────────▼──────────┐
              │ PostgreSQL + pgvector                          │
              │  base openwebui : interne Open WebUI           │
              │  base contrats  : référentiel, chunks, vecteurs│◄── ingest (CLI batch)
              └────────────────────────────────────────────────┘    OCR, extraction
              Fichiers : data/open-webui, data/contrats/originals
              API internes : LLM · Embeddings · Reranker · OCR/Vision
```

## Portabilité

- **Un seul fichier de configuration** : `.env`. Il porte les endpoints des modèles, les
  secrets, les chemins et la version épinglée.
- **Tout l'état est dans `DATA_ROOT`**. Pour changer de serveur, il suffit de
  `make backup`, de copier le résultat, puis de lancer `install.sh` et `restore.sh` sur le
  nouveau serveur.
- **Installation hors ligne** :
  1. Sur une machine connectée : `make bundle`. Cela produit
     `dist/contrats-rag-<version>.tar.gz`, qui contient les images Docker, les scripts et
     la documentation.
  2. Sur le serveur cible, sans Internet :
     ```bash
     tar xzf contrats-rag-0.1.0.tar.gz && cd contrats-rag-0.1.0
     ./scripts/install.sh      # 1er passage : crée .env et génère les secrets
     nano .env                 # endpoints internes des modèles
     ./scripts/install.sh      # charge les images, démarre, migre, contrôle
     ```
- **Prérequis serveur** : Linux x86_64, Docker Engine 24+ avec le plugin Compose v2,
  environ 15 Go de disque pour les images et les données de départ, 8 Go de RAM.
- Si les scripts ont été copiés depuis Windows et perdu leur droit d'exécution :
  `chmod +x scripts/*.sh`, ou bien lancer `bash scripts/install.sh`.

## Mise en service

1. **Installer** : `./scripts/install.sh` (voir ci-dessus). Pour le mode connecté, lancez
   ce script directement depuis ce dépôt.
2. **Connecter les modèles** : renseigner `.env`, puis `make restart && make check`.
   Voir [docs/CONNEXION_MODELES.md](docs/CONNEXION_MODELES.md).
3. **Créer le compte administrateur** : ouvrir `http://<serveur>:3000`. Le premier compte
   créé est administrateur. Mettre ensuite `ENABLE_SIGNUP=false` dans `.env` et lancer
   `make restart`, ou brancher LDAP/OIDC.
4. **Importer le référentiel** (optionnel) : voir [docs/IMPORT_CONTRATS.md](docs/IMPORT_CONTRATS.md).
5. **Ingérer les documents** : déposer les fichiers dans `inbox/<REFERENCE_CONTRAT>/`
   (voir [inbox/README.md](inbox/README.md)), puis `make ingest`. Les fichiers déjà indexés
   sont ignorés. Les PDF scannés passent automatiquement par l'OCR.
6. **Tester la recherche** : `make search Q="pénalités de retard"` et
   `make show REF=<référence>`.
7. **Publier les extensions** dans Open WebUI : le Tool `contrats_recherche` et la Function
   filtre `contrats_contexte`, qui fait une recherche systématique et contrôle la réponse.
   - Profil > Paramètres > Compte > Clés API, puis `make deploy-tools KEY=sk-…` ;
   - ou bien coller les fichiers de `owui_extensions/tools/` (Espace de travail > Outils) et de
     `owui_extensions/functions/` (Panneau d'administration > Fonctions).
8. **Créer le modèle « Assistant Contrats »** (Espace de travail > Modèles) :
   - modèle de base : le LLM interne ;
   - prompt système : `owui_extensions/prompts/assistant_contrats_system.md` ;
   - **filtre : cocher « Contrats – Recherche systématique (RAG forcé) »**. Il est
     indispensable : en test, le LLM n'appelait pas les outils de lui-même
     (voir GUIDE_DEPLOIEMENT.md §12) ;
   - outil : cocher « Contrats – Recherche et consultation » ;
   - paramètres avancés : appel de fonctions **natif** ;
   - accès : les groupes concernés.
9. **Définir les droits métier**. Les Tools ne bénéficient pas des droits Open WebUI sur les
   bases de connaissances. Les règles se posent avec la CLI :
   ```bash
   docker compose run --rm ingest access grant "group:Etudes"                      # tous les contrats
   docker compose run --rm ingest access grant "user:x@iap.dz" --contrat REF-001   # un contrat
   ```
   Puis passer `DEFAULT_CONTRACT_ACCESS=none` en production.

## Commandes d'exploitation

`make help` liste tous les raccourcis. Les principaux :

| Commande | Rôle |
|---|---|
| `make check` | Teste la base, le stockage, le LLM, les embeddings, le reranker et l'OCR |
| `make ingest` | Ingère `inbox/` |
| `make reindex` | Vectorise ce qui était en attente (endpoint indisponible lors de l'ingestion) |
| `docker compose run --rm ingest reindex --all` | Recalcule tout après un changement de modèle d'embedding |
| `make documents` | Statut des documents (`indexe`, `a_vectoriser`, `erreur`) |
| `make backup` / `make restore DIR=…` | Sauvegarde et restauration complètes |
| `make logs` | Journaux d'Open WebUI |

## Organisation du code

```
compose.yml, .env.example     déploiement (configuration unique)
docker/                       Dockerfiles, init PostgreSQL (2 bases, 3 rôles)
scripts/                      install, bundle hors ligne, sauvegarde, restauration
src/contract_rag/
  config/settings.py          toute la configuration (variables CRAG_*)
  gateways/                   clients LLM, embeddings, reranker, OCR (OpenAI-compatibles)
  db/                         connexion, migrations SQL versionnées
  domain/                     calculs purs : montants, délais, pénalités, parsing FR
  ingestion/                  lecteurs PDF/Word/Excel, OCR, structure par articles, pipeline
  retrieval/hybrid.py         recherche hybride SQL (plein texte FR + vecteurs, RRF)
  repositories/               accès SQL
  services/                   recherche, fiche contrat, contrôles de cohérence
  security/authz.py           périmètre d'accès métier par utilisateur ou groupe
  presentation.py             mise en forme Markdown et citations
  owui/                       SEUL module lié à Open WebUI (adaptateurs, déploiement)
  cli.py                      commande contract-rag
owui_extensions/              Tools, Function filtre et prompts publiés dans Open WebUI (code minimal)
deploy.sh                     déploiement en une commande (serveur Ubuntu/Debian vierge)
.githooks/                    garde-fous Git : aucun secret ni document dans un commit ou un push
DEPLOIEMENT_GIT.md            GitHub privé, commits automatiques, git clone + deploy.sh
ARCHITECTURE.md               architecture complète et déploiement rapide ailleurs
GUIDE_DEPLOIEMENT.md          copie complète du projet et déploiement sur un autre serveur
tests/                        tests unitaires (sans base, sans modèle)
docs/                         connexion des modèles, import, points à vérifier
```

Règle de dépendances : `owui_extensions → contract_rag.owui → services → domain / repositories / gateways`.
Le package ne dépend pas d'Open WebUI. Une future API (FastAPI, ou serveur d'outils
OpenAPI/MCP) pourra réutiliser `services/` sans réécriture.

## Développement

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,ingest]"
pytest -q
```

## État d'avancement

Réalisé : déploiement portable, ingestion traçable (page, section, tableau, cellule), OCR,
recherche hybride avec reranking, Tool de recherche et de consultation, droits métier,
contrôles de cohérence, calculs financiers et de délais, sauvegarde et restauration.

Prochaines étapes (phase 4) :
- extraction structurée par LLM des champs contractuels (table `champ_extrait`, avec
  extrait justificatif) ;
- validation par aller-retour Excel ;
- Tools de comparaison et d'analyse des avenants ;
- exports Excel et Word.

Points à valider en POC : [docs/POINTS_A_VERIFIER.md](docs/POINTS_A_VERIFIER.md).
