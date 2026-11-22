# Guide de déploiement – Contrats RAG

Ce guide explique comment **copier le projet complet** et le **déployer sur n'importe quel
serveur Linux** avec Docker, y compris toutes les configurations : modèles, sécurité,
comptes, sauvegardes, mises à jour.

> Ce guide a été rédigé et testé en déployant le projet sur Ubuntu 24.04 (WSL2),
> Docker 29.1.3, Compose 2.40.3. Voir la section 11.

---

## 1. Ce qu'il faut savoir avant de commencer

| Élément | Rôle | Où |
|---|---|---|
| `compose.yml` | Décrit les 3 services : `postgres`, `open-webui`, `ingest` | racine |
| `.env` | **Seul fichier de configuration** : modèles, secrets, ports, chemins | racine, créé à l'installation |
| `data/` | **Tout l'état** : bases PostgreSQL, fichiers Open WebUI, documents originaux | `DATA_ROOT` |
| `inbox/` | Dépôt des documents à ingérer (un sous-dossier par contrat) | `INBOX_DIR` |
| `certs/` | Certificats des autorités internes (HTTPS des API modèles) | racine |
| `scripts/` | Installation, paquet hors ligne, sauvegarde, restauration | racine |

Trois façons de déployer :

| Situation | Méthode |
|---|---|
| Le serveur cible a accès aux registres Docker / Internet | **A – Copie du projet + installation** (§3) |
| Le serveur cible n'a **pas** Internet | **B – Paquet hors ligne** (§4) |
| Déplacer une installation existante **avec ses données** | **C – Migration** (§5) |

---

## 2. Préparer le serveur cible

### 2.1 Prérequis matériels
- Linux x86_64 (Ubuntu 22.04/24.04, Debian 12, RHEL/Rocky 9…).
- 4 vCPU, **8 Go de RAM minimum** (16 Go conseillés), 30 Go de disque, plus le volume des documents.
- Ports : `3000/tcp` (interface web, modifiable). PostgreSQL n'est **pas** exposé.
- Accès réseau aux API internes des modèles (LLM, embeddings, reranker, OCR vision).

### 2.2 Installer Docker et Compose v2

**Ubuntu / Debian** (paquets de la distribution, méthode testée) :
```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 make
sudo systemctl enable --now docker
```

**RHEL / Rocky / Alma** (dépôt officiel Docker) :
```bash
sudo dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin make
sudo systemctl enable --now docker
```

Pour un serveur sans Internet, installez Docker depuis le **miroir interne** de paquets
(Nexus, Artifactory, Satellite…) ou depuis des paquets `.deb`/`.rpm` copiés au préalable.

Vérification :
```bash
docker --version          # Docker 24 ou plus
docker compose version    # Compose v2
```

Optionnel, pour lancer Docker sans `sudo` : `sudo usermod -aG docker $USER`, puis se reconnecter.

---

## 3. Méthode A – Copier le projet et l'installer (serveur connecté)

### 3.1 Créer l'archive du projet (poste Windows)

Dans PowerShell, depuis le dossier du projet. Les fichiers locaux et l'état sont exclus :
```powershell
tar -czf contrats-rag.tar.gz --exclude=.venv --exclude=data --exclude=exports --exclude=backups --exclude=dist --exclude=__pycache__ --exclude=.pytest_cache --exclude=*.egg-info --exclude=.env .
```

> Ne copiez **jamais** `.venv/` (spécifique à Windows). Ne copiez `data/` que dans le cadre
> d'une migration (§5). Le `.env` contient des secrets : il est régénéré sur le serveur.

### 3.2 Transférer l'archive
```powershell
scp contrats-rag.tar.gz utilisateur@serveur:/tmp/
```
Autres moyens : WinSCP, partage réseau, clé USB, ou `git clone` si le projet est
versionné dans le GitLab interne.

### 3.3 Décompresser sur le serveur
```bash
sudo mkdir -p /opt/contrats-rag
sudo tar -xzf /tmp/contrats-rag.tar.gz -C /opt/contrats-rag
cd /opt/contrats-rag
sudo chmod +x scripts/*.sh docker/postgres/init/*.sh
```

### 3.4 Premier passage : création de `.env`
```bash
sudo ./scripts/install.sh
```
Le script crée `.env`, génère les 5 secrets (mots de passe PostgreSQL, clé Open WebUI)
puis s'arrête pour que vous renseigniez les modèles.

### 3.5 Configurer `.env`
```bash
sudo nano .env
```
Variables **obligatoires** à adapter (détails au §6) :
```env
LLM_BASE_URL=http://<serveur-llm>:8000/v1
LLM_MODEL=<nom exposé par /v1/models>
EMBEDDING_BASE_URL=http://<serveur-embeddings>:8080/v1
EMBEDDING_MODEL=<modèle multilingue>
EMBEDDING_DIM=<dimension du modèle>     # ex. 1024 pour bge-m3, 768 pour nomic-embed-text
RERANKER_API=cohere | tei | none
AI_ALLOWED_HOSTS=<hôtes ou .domaine des API internes>
```

### 3.6 Second passage : installation
```bash
sudo ./scripts/install.sh
```
Le script :
1. construit les images (`contrats-rag/open-webui`, `contrats-rag/ingest`) ;
2. démarre PostgreSQL, qui crée les bases `openwebui` et `contrats` et les 3 rôles ;
3. applique le schéma métier ;
4. démarre Open WebUI ;
5. teste chaque endpoint de modèle (`check`).

Résultat attendu :
```
✅ Base contrats         PostgreSQL 17.x, pgvector 0.8.0
✅ Stockage documents    /data/contrats
✅ LLM                   <modèle> @ <url> → « OK »
✅ Embeddings            <modèle> @ <url>, dimension <N>
```
Puis passez au §7 (mise en service).

---

## 4. Méthode B – Serveur sans Internet (paquet hors ligne)

### 4.1 Sur une machine connectée (Linux ou WSL avec Docker)
```bash
cd contrats-rag            # le projet copié comme au §3
./scripts/bundle-offline.sh
```
Produit `dist/contrats-rag-<version>.tar.gz` (≈ 1,8 Go) et son empreinte `.sha256`.

Plusieurs instances sur le même serveur (recette et production, par exemple) : donner à
chacune un dossier, un `OWUI_PORT` et un nom de projet distincts
(`export COMPOSE_PROJECT_NAME=recette` avant `install.sh` et les commandes `docker compose`).
Le paquet contient :
- les **3 images Docker** exportées (Open WebUI dérivée, ingest, PostgreSQL + pgvector) ;
- tous les fichiers de déploiement, la documentation et les sources.

Pour que le serveur cible fasse confiance aux API internes en HTTPS, déposez **avant** ce
build les certificats des autorités internes dans `certs/*.crt`.

### 4.2 Transfert et installation sur le serveur isolé
```bash
sha256sum -c contrats-rag-0.1.0.tar.gz.sha256      # contrôle d'intégrité
tar -xzf contrats-rag-0.1.0.tar.gz -C /opt
cd /opt/contrats-rag-0.1.0
./scripts/install.sh        # 1er passage : crée .env
nano .env                   # endpoints internes (§6)
./scripts/install.sh        # charge les images (docker load), démarre, migre, contrôle
```
Aucun accès à Internet n'est nécessaire sur le serveur cible : le script détecte le dossier
`images/` et charge les images au lieu de les construire.

---

## 5. Méthode C – Migrer une installation existante (avec ses données)

### 5.1 Sur l'ancien serveur
```bash
cd /opt/contrats-rag
./scripts/backup.sh /srv/sauvegardes
```
Crée `/srv/sauvegardes/AAAAMMJJ-HHMMSS/`. Le dossier contient :
- `roles.sql`, `openwebui.dump`, `contrats.dump` : les bases ;
- `fichiers.tar.gz` : les fichiers Open WebUI et les documents originaux ;
- `env.backup` : la configuration, **avec ses secrets** ;
- `SHA256SUMS`.

### 5.2 Sur le nouveau serveur
1. Installer Docker (§2) et copier le projet (§3.1 à 3.3, ou §4).
2. **Reprendre la configuration de l'ancien serveur** (mêmes mots de passe) :
   ```bash
   cp /chemin/sauvegarde/env.backup /opt/contrats-rag/.env
   ```
   Adaptez si besoin `DATA_ROOT`, `OWUI_PORT` et les URL des modèles.
3. Installer : `./scripts/install.sh`
4. Restaurer :
   ```bash
   ./scripts/restore.sh /chemin/sauvegarde/AAAAMMJJ-HHMMSS     # taper OUI pour confirmer
   ```
5. Vérifier : `make check`, se connecter, lancer une recherche.

Copier directement le dossier `data/` d'un serveur arrêté (`docker compose down`) vers le
nouveau fonctionne aussi, à condition que la version de PostgreSQL soit identique.
La méthode par sauvegarde reste plus sûre.

---

## 6. Référence de configuration (`.env`)

### 6.1 Modèles d'IA (API internes, format OpenAI)
| Variable | Exemple | Remarque |
|---|---|---|
| `LLM_BASE_URL` | `http://llm.interne.local:8000/v1` | inclut `/v1` |
| `LLM_API_KEY` | *(vide)* | envoyée en `Bearer` si renseignée |
| `LLM_MODEL` | `mistral-small` | nom renvoyé par `GET /v1/models` |
| `EMBEDDING_BASE_URL` / `_MODEL` / `_API_KEY` | | |
| `EMBEDDING_DIM` | `1024` | **à fixer avant la 1re ingestion** |
| `EMBEDDING_QUERY_PREFIX` / `_DOCUMENT_PREFIX` | `"query: "` | requis par les modèles e5 ou nomic. Mettre des guillemets s'il y a un espace. |
| `RERANKER_API` | `cohere` | `cohere` (vLLM, Infinity, Jina), `tei`, `none` |
| `RERANKER_BASE_URL` / `_MODEL` / `_API_KEY` | | |
| `OCR_ENGINE` | `tesseract` | `tesseract` (local, inclus), `vision` (modèle interne), `none` |
| `OCR_LANGUAGES` | `fra+eng` | langues Tesseract |
| `VISION_BASE_URL` / `_MODEL` / `_API_KEY` | | si `OCR_ENGINE=vision` |

Exemples par type de serveur (vLLM, TEI, Ollama, passerelle) : [docs/CONNEXION_MODELES.md](docs/CONNEXION_MODELES.md).

### 6.2 Sécurité réseau
| Variable | Rôle |
|---|---|
| `AI_ALLOWED_HOSTS` | liste blanche des hôtes IA. Tout autre hôte est refusé par la logique métier. Ex. `.interne.local,10.20.30.40` |
| `AI_VERIFY_TLS` | `true` : vérifie les certificats des API (CA internes dans `certs/`) |
| `OWUI_BIND` | `127.0.0.1` pour n'exposer l'interface qu'au proxy HTTPS local |
| `OWUI_PORT` | port publié de l'interface (défaut `3000`) |

Le **blocage des sorties Internet** se fait au pare-feu : voir l'exemple `DOCKER-USER` dans
[docs/POINTS_A_VERIFIER.md](docs/POINTS_A_VERIFIER.md).

### 6.3 Certificats internes
```bash
cp autorite-racine.crt certs/
docker compose --profile cli build     # intègre les CA dans les images
docker compose up -d
```

### 6.4 Stockage
| Variable | Défaut | Rôle |
|---|---|---|
| `DATA_ROOT` | `./data` | bases + fichiers. Mettre un volume dédié, ex. `/srv/contrats-rag/data` |
| `INBOX_DIR` | `./inbox` | dossier de dépôt, monté en lecture seule dans `ingest` |

### 6.5 Comptes et accès
| Variable | Rôle |
|---|---|
| `ENABLE_SIGNUP` | `true` pour créer le 1er compte (admin), **puis `false`** |
| `DEFAULT_USER_ROLE` | `pending` : les nouveaux comptes attendent une validation |
| `DEFAULT_CONTRACT_ACCESS` | `all` (POC) ou `none` (production : droits explicites, §7.5) |

L'authentification d'entreprise (LDAP / Active Directory, OIDC / ADFS / Keycloak) se
configure dans Open WebUI : Admin > Paramètres, ou via ses variables d'environnement
ajoutées à `compose.yml`. Les noms exacts sont à vérifier sur la version épinglée.

### 6.6 Après toute modification de `.env`
```bash
docker compose up -d --force-recreate open-webui    # ou : make restart
docker compose run --rm ingest check                 # ou : make check
```

---

## 7. Mise en service (après l'installation)

1. **Compte administrateur** : ouvrir `http://<serveur>:3000` et créer le premier compte, qui
   devient administrateur. Mettre ensuite `ENABLE_SIGNUP=false` puis `make restart`.
2. **Référentiel** (optionnel) : `contracts import` d'un CSV, voir [docs/IMPORT_CONTRATS.md](docs/IMPORT_CONTRATS.md).
3. **Documents** : copier les fichiers dans `inbox/<REFERENCE>/`, puis
   ```bash
   docker compose run --rm ingest ingest /inbox
   docker compose run --rm ingest documents          # statut par document
   docker compose run --rm ingest search "pénalités de retard"
   ```
4. **Extensions dans Open WebUI** : le Tool `contrats_recherche` et la Function filtre
   `contrats_contexte`. Deux possibilités :
   - créer une clé API (Profil > Paramètres > Compte > Clés API), puis
     `docker compose run --rm ingest owui deploy --api-key sk-…`. Les deux extensions sont
     publiées et la Function est activée ;
   - ou les coller à la main : le Tool dans Espace de travail > Outils > « + », la Function
     dans Panneau d'administration > Fonctions > « + ».
5. **Modèle « Assistant Contrats »** (Espace de travail > Modèles > « + ») :
   - modèle de base : le LLM interne ;
   - prompt système : `owui_extensions/prompts/assistant_contrats_system.md` ;
   - **filtres : cocher « Contrats – Recherche systématique (RAG forcé) »**. C'est
     indispensable si le LLM n'appelle pas les outils de façon fiable (voir §12) ;
   - outils : cocher « Contrats – Recherche et consultation », pour la fiche contrat et la
     liste des contrats, si le LLM sait appeler des outils ;
   - paramètres avancés : appel de fonctions **natif** ;
   - visibilité : réserver le modèle aux groupes concernés.
6. **Droits métier**, en production avec `DEFAULT_CONTRACT_ACCESS=none` :
   ```bash
   docker compose run --rm ingest access grant "group:Etudes"                    # tous les contrats
   docker compose run --rm ingest access grant "user:nom@iap.dz" --contrat REF   # un contrat
   docker compose run --rm ingest access list
   ```

---

## 8. Exploitation courante

| Action | Commande |
|---|---|
| État des services | `docker compose ps` |
| Journaux | `docker compose logs -f open-webui` |
| Arrêt / démarrage | `docker compose down` / `docker compose up -d` |
| Contrôle des modèles | `make check` |
| Nouveaux documents | dépôt dans `inbox/` puis `make ingest` (les fichiers déjà indexés sont ignorés) |
| Vectorisation en attente | `make reindex` |
| Changement de modèle d'embedding | modifier `.env`, puis `docker compose run --rm ingest reindex --all` |
| Sauvegarde | `./scripts/backup.sh` (planifiable, ex. cron quotidien) |

Sauvegarde automatique quotidienne à 2 h (crontab de root) :
```cron
0 2 * * * cd /opt/contrats-rag && ./scripts/backup.sh /srv/sauvegardes >> /var/log/contrats-rag-backup.log 2>&1
```

Démarrage automatique au boot : les services ont `restart: unless-stopped`. Il suffit que
Docker soit activé (`systemctl enable docker`).

---

## 9. Mettre à jour l'application

1. Sauvegarder : `./scripts/backup.sh`.
2. Remplacer les fichiers du projet par la nouvelle version, **sans toucher à `.env` ni à `data/`**.
3. Comparer `.env.example` avec `.env` et ajouter les nouvelles variables éventuelles.
4. Lancer `./scripts/install.sh` : il reconstruit les images ou charge le nouveau paquet,
   puis applique les nouvelles migrations.

Changer de version d'Open WebUI : modifier `OWUI_VERSION` dans `.env` **après test en
recette**. Les extensions et l'API d'Open WebUI évoluent vite.

---

## 10. Dépannage

| Symptôme | Cause probable | Solution |
|---|---|---|
| `❌ LLM … refusé : l'hôte … n'est pas dans CRAG_ALLOWED_HOSTS` | liste blanche | ajouter l'hôte dans `AI_ALLOWED_HOSTS` |
| `❌ Embeddings … Dimension 768 ≠ CRAG_EMBEDDING_DIM=1024` | mauvaise dimension | corriger `EMBEDDING_DIM`, puis `reindex --all` |
| `ConnectTimeout` vers les modèles | réseau ou pare-feu | `docker compose run --rm --entrypoint curl ingest -s <URL>/models` |
| `CERTIFICATE_VERIFY_FAILED` | CA interne absente | §6.3 |
| Interface inaccessible | port ou pare-feu | `docker compose ps`, `ss -ltnp \| grep 3000`, pare-feu du serveur |
| Document en `erreur` | fichier illisible ou protégé | `make documents` (message), puis `ingest <fichier> --force` |
| Document en `a_vectoriser` | embeddings indisponibles lors de l'ingestion | `make reindex` |
| Page `vide` dans un PDF | scan sans OCR | `OCR_ENGINE=tesseract` (défaut) puis `ingest … --force` |
| PostgreSQL ne démarre pas | droits sur `DATA_ROOT/postgres` | `docker compose logs postgres` |
| `.doc` / `.xls` refusés | anciens formats | construire `ingest` avec LibreOffice (voir ci-dessous) |
| `lookup … i/o timeout` ou `network is unreachable` pendant le build | DNS instable, ou IPv6 sans route | les scripts relancent automatiquement (5 essais) ; sinon relancer `install.sh` |
| L'assistant invente des chiffres | le LLM n'appelle pas les outils, ou recalcule lui-même | associer le filtre « RAG forcé » au modèle (§7) ; le garde-fou signale les montants non sourcés |

Prise en charge des anciens formats Office :
```bash
docker compose build --build-arg INSTALL_LIBREOFFICE=true ingest
```

---

## 11. Déployer sous Windows avec WSL2 (poste de test ou de démonstration)

WSL2 avec Ubuntu se comporte comme un serveur Linux. C'est la configuration utilisée pour
valider ce guide (§12).
```powershell
wsl --install -d Ubuntu-24.04
```
Puis, dans Ubuntu : §2.2 (Docker), et §3 en décompressant l'archive dans `/opt/contrats-rag`.
Copier le projet depuis Windows :
```bash
sudo mkdir -p /opt/contrats-rag
cd /mnt/c/Users/<utilisateur>/Desktop/new
sudo tar --exclude=.venv --exclude=data --exclude=.env -cf - . | sudo tar -C /opt/contrats-rag -xf -
```
Particularités de WSL :
- l'interface est accessible depuis Windows sur `http://localhost:3000` ;
- un service qui tourne **sous Windows** (par exemple Ollama) et n'écoute que sur `127.0.0.1`
  n'est **pas joignable** depuis WSL. Il faut le faire écouter sur l'interface réseau, ou faire
  tourner le service modèle dans WSL ;
- l'IP de WSL (`hostname -I`) peut changer au redémarrage. Si `.env` pointe vers cette IP,
  mettez-la à jour ;
- le DNS de WSL peut être instable : les scripts réessaient automatiquement.

---

## 12. Compte rendu du test de déploiement (21/11/2026)

**Environnement** : Windows 11 + WSL2 Ubuntu 24.04.5, 16 cœurs, 7 Go de RAM, Docker 29.1.3,
Compose 2.40.3, installés avec `apt install docker.io docker-compose-v2`.
Projet copié dans `/opt/contrats-rag` selon la méthode du §11.

**Serveur d'API IA interne simulé** : un conteneur Ollama séparé de l'application, joint
par l'IP du serveur, comme une API interne :
- LLM `mistral:7b-instruct` ;
- embeddings `nomic-embed-text` (dimension 768, préfixes `search_query: ` / `search_document: `) ;
- pas de reranker ;
- `AI_ALLOWED_HOSTS` = IP du serveur.

**Documents de test fictifs** (`inbox/TEST-2026-001/`) :
- un contrat PDF de 3 pages, dont la page 3 est **scannée** (image seule) ;
- l'avenant n°1 en Word ;
- un BPU en Excel.

| # | Test | Résultat |
|---|---|---|
| 1 | `install.sh` en 2 passages : génération des secrets, build, PostgreSQL, migrations, Open WebUI | ✅ (2 échecs réseau passagers rattrapés par les nouvelles tentatives) |
| 2 | `check` : PostgreSQL 17.6 + pgvector 0.8.0, stockage, LLM, embeddings (dimension vérifiée), OCR Tesseract 5.3 fra+eng | ✅ |
| 3 | Import CSV du référentiel | ✅ |
| 4 | Ingestion des 3 formats, type déduit (contrat, avenant n°1, BPU), **OCR de la page scannée** | ✅ 14 chunks |
| 5 | Embeddings indisponibles pendant l'ingestion : statut `a_vectoriser`, puis `reindex` | ✅ |
| 6 | Ré-ingestion : fichiers ignorés (même empreinte) | ✅ |
| 7 | Recherche lexicale seule quand les embeddings sont coupés | ✅ (avec avertissement) |
| 8 | Recherche sémantique : « combien le titulaire paie-t-il s'il livre en retard ? » renvoie l'article 7 « Pénalités de retard », p. 2 | ✅ |
| 9 | Citations : page (PDF), section (Word), plage de cellules `'BPU'!A2:D5` (Excel) | ✅ |
| 10 | Open WebUI sur PostgreSQL (base `openwebui`, 54 tables), séparée de la base `contrats` | ✅ |
| 11 | Publication du Tool et de la Function par l'API (`owui deploy`) ; package `contract_rag` importé dans Open WebUI | ✅ |
| 12 | Question à l'assistant **avec le Tool seul** | ❌ Mistral 7B n'appelle pas réellement l'outil et **invente** la réponse, aussi en mode natif |
| 13 | Question à l'assistant **avec le filtre « RAG forcé »** | ✅ réponses exactes et sourcées ; « garantie décennale » répond « non présente dans les documents » |
| 14 | Citations affichées dans l'interface (6 sources : fichier, page, tableau) | ✅ |
| 15 | Montant recalculé par le LLM (TTC faux) | ✅ signalé par le garde-fou de sortie |
| 16 | Sauvegarde, suppression volontaire des documents, restauration : 14 chunks retrouvés, compte, Tool, Function et conversations restaurés | ✅ |
| 17 | `bundle-offline.sh` : paquet de **1,8 Go** (open-webui 1,6 Go, ingest 122 Mo, postgres 149 Mo), empreinte `sha256` vérifiée | ✅ en 3 min |
| 18 | Installation du paquet sur un « nouveau serveur » : 2e instance isolée (`COMPOSE_PROJECT_NAME=offline-test`, port 3001). Images **chargées** et non construites, migrations, `check`, ingestion de 3 documents, recherche, interface en HTTP 200 | ✅ |

**Enseignements intégrés au projet** :
- Le **filtre « RAG forcé »** est indispensable : on ne peut pas compter sur le LLM pour
  appeler les outils. Il est désormais livré (`owui_extensions/functions/contrats_contexte.py`)
  et activé par `owui deploy`.
- Un **garde-fou de sortie** signale tout montant, date ou quantité de la réponse absent des
  sources. Limite : un montant présent dans les sources mais mal utilisé n'est pas détecté.
  Les calculs fiables (avenants, délais) viendront des Tools de la phase 4, et un LLM interne
  plus capable qu'un modèle 7B est recommandé.
- **Nouvelles tentatives réseau** ajoutées à `install.sh` et `bundle-offline.sh`.
- Lecture des groupes Open WebUI : l'API est asynchrone en v0.13, l'adaptateur est corrigé.
- Affichage de la TVA corrigé (« 19 % »), réglage CORS ajouté (`WEBUI_URL`, `CORS_ALLOW_ORIGIN`).
- Performances sur le processeur (7B, sans GPU) : 45 à 180 s par réponse. Sur une API
  interne avec GPU, ce temps tombe à quelques secondes.
