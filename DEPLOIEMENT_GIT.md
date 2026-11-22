# Déployer Contrats RAG depuis un dépôt GitHub privé

Ce document explique comment :
1. publier le projet sur un **dépôt GitHub privé**, sans aucune donnée confidentielle ;
2. travailler avec les **commits automatiques locaux**, en gardant la main sur `git push` ;
3. **déployer sur un serveur Ubuntu vierge** avec `git clone` puis une seule commande, `sudo ./deploy.sh` ;
4. mettre à jour, migrer ou dépanner une installation.

Rien dans cette procédure ne dépend du poste Windows actuel : un serveur qui peut lire le
dépôt GitHub et joindre les API internes des modèles suffit.

> Autres documents : [ARCHITECTURE.md](ARCHITECTURE.md) (fonctionnement),
> [DEPLOIEMENT_UBUNTU.md](DEPLOIEMENT_UBUNTU.md) (déploiement par copie d'archive, HTTPS,
> pare-feu), [GUIDE_DEPLOIEMENT.md](GUIDE_DEPLOIEMENT.md) (référence de `.env`, dépannage).

---

## 1. Ce que contient le dépôt et ce qu'il ne contiendra jamais

### 1.1 Versionné : tout ce qu'il faut pour déployer
| Élément | Fichiers |
|---|---|
| Déploiement en une commande | `deploy.sh` |
| Conteneurs | `compose.yml`, `docker/openwebui.Dockerfile`, `docker/ingest.Dockerfile`, `docker/postgres/init/01-init.sh` |
| Configuration **modèle**, sans secret | `.env.example` (tous les paramètres propres au serveur y sont documentés) |
| Dépendances | `pyproject.toml`. Les versions d'Open WebUI et de PostgreSQL sont épinglées dans `.env.example`. |
| Scripts d'exploitation | `scripts/install.sh`, `backup.sh`, `restore.sh`, `bundle-offline.sh`, `auto-commit.sh`, `git-setup.sh`, `Makefile` |
| Code métier et extensions | `src/contract_rag/`, `owui_extensions/` |
| Tests et évaluation | `tests/`, `eval/questions_reference.csv` (questions fictives) |
| Documentation | `*.md`, `docs/` (exemple de CSV **fictif**) |
| Garde-fous Git | `.gitignore`, `.gitattributes`, `.dockerignore`, `.githooks/` |
| Outillage Claude Code | `.claude/settings.json` (hook de commit automatique) |

### 1.2 Jamais versionné (bloqué deux fois)
Ces éléments sont exclus par `.gitignore`. Si l'un d'eux est forcé avec `git add -f`, il est
refusé par le contrôle `.githooks/pre-commit`, puis par `.githooks/pre-push` avant tout envoi :

| Catégorie | Exemples |
|---|---|
| Secrets | `.env`, `.env.*`, `*.env`, `.test-admin*`, `secrets/`, clés API (`sk-…`), jetons GitHub, mots de passe en clair |
| Clés et certificats | `*.key`, `*.pem`, `*.p12`, `*.pfx`, `id_rsa*`, `certs/*` (sauf le README) |
| Documents contractuels | `inbox/*`, `*.pdf`, `*.docx`, `*.doc`, `*.xlsx`, `*.xls`, `*.odt`, `*.tif`, `*.msg`… |
| Bases et sauvegardes | `data/`, `backups/`, `*.dump`, `*.sqlite`, `*.db`, `*.bak` |
| Index vectoriels | `*.faiss`, `*.index`, `chroma/`, `qdrant_storage/` (avec pgvector, les vecteurs sont dans `data/postgres`, déjà exclu) |
| Modèles d'IA | `models/`, `ollama/`, `*.gguf`, `*.safetensors`, `*.onnx`, `*.pt`, `*.bin` |
| Paquets et images | `dist/`, `images/`, `*.tar.gz` (le paquet hors ligne fait environ 2 Go) |
| Données utilisateurs et divers | `exports/`, `uploads/`, `*.log`, `.venv/`, caches, `.claude/settings.local.json` |
| Gros fichiers | tout fichier de plus de 5 Mo |

### 1.3 Résultat de l'audit (22/11/2026)
- 81 fichiers versionnés. Aucun `.env`, aucun document, aucune base, aucun modèle.
- Le scan du contenu ne trouve ni clé API, ni mot de passe, ni adresse IP du réseau de
  test, ni adresse e-mail personnelle.
- Seules données d'exemple : `docs/exemples/contrats.csv` (contrat `EXEMPLE-2023-001`,
  « ENTREPRISE FICTIVE SARL ») et des questions de test.
- Les données de l'installation de test WSL (`/opt/contrats-rag`) restent en dehors du dépôt
  et n'ont pas été modifiées.
- Fins de ligne : forcées en LF (`.gitattributes`), pour que les scripts fonctionnent sous
  Linux même s'ils ont été modifiés sous Windows.
- Les droits d'exécution (`100755`) sont enregistrés dans Git : `deploy.sh`, `scripts/*.sh`,
  `.githooks/*`, `docker/postgres/init/*.sh`.

---

## 2. Publier le projet sur GitHub (une seule fois, depuis le poste Windows)

Le dépôt local existe déjà (`git log` montre l'historique). **Aucun push n'a été fait.**

### 2.1 Choisir l'identité qui apparaîtra sur GitHub
Les commits ont été créés avec une identité neutre, `Contrats RAG <contrats-rag@localhost.invalid>`,
pour ne publier aucune donnée personnelle sans votre accord. Remplacez-la par la vôtre ; l'adresse
*noreply* de GitHub évite d'exposer votre e-mail :
```powershell
cd C:\Users\DELL\Desktop\new
git config user.name "Prénom Nom"
git config user.email "identifiant@users.noreply.github.com"
```
Pour réattribuer aussi les commits déjà faits, **avant le premier push uniquement** :
```powershell
git rebase --root --exec "git commit --amend --no-edit --reset-author --no-verify"
```

### 2.2 Créer le dépôt privé
Sur github.com : **New repository** → nom `contrats-rag` → **Private** → ne cochez **pas**
« Add a README », ni `.gitignore`, ni licence (le dépôt doit être vide).

### 2.3 Relier le dépôt et vérifier avant d'envoyer
```powershell
bash scripts/git-setup.sh git@github.com:<organisation>/contrats-rag.git
git ls-files
git log --stat
```
`git-setup.sh` active les garde-fous et déclare le dépôt distant **sans rien envoyer**.
Relisez la liste des fichiers : elle doit correspondre au §1.1.

### 2.4 Premier envoi (à votre initiative)
```powershell
git push -u origin main
```
Le hook `pre-push` revérifie tout le contenu envoyé. S'il détecte un secret, le push est
annulé et rien n'est envoyé.

Authentification : clé SSH ajoutée à votre compte GitHub (recommandé), ou HTTPS avec
Git Credential Manager (fourni avec Git pour Windows).

---

## 3. Commits automatiques (locaux, sans push)

### 3.1 Fonctionnement
| Déclencheur | Ce qui se passe |
|---|---|
| Fin de chaque intervention de Claude Code dans ce projet | Le hook `Stop` (`.claude/settings.json`) lance `scripts/auto-commit.sh --hook`. Toute modification devient un commit local. |
| Vos propres modifications | `make commit`, ou `bash scripts/auto-commit.sh` : le message est généré automatiquement |
| Vos modifications avec votre message | `make commit M="Correction du calcul des pénalités"` |

Règles appliquées à chaque commit :
- `.gitignore` est respecté, et le garde-fou anti-secrets s'exécute : un commit suspect est
  **refusé** et les fichiers restent non validés ;
- les changements purement cosmétiques (espaces, fins de ligne) ne créent pas de commit ;
- **jamais de `git push`** : la publication reste une action manuelle.

Exemple de message généré :
```
Mise à jour code ingestion, documentation : 2 modifié(s), 1 ajouté(s)

Domaines : code ingestion, documentation
3 files changed, 41 insertions(+), 6 deletions(-)

- modif. src/contract_rag/ingestion/pipeline.py
- modif. README.md
- ajout  tests/test_pipeline_ocr.py

(commit automatique local – aucun push)
```
Les modifications importantes faites par Claude reçoivent un message rédigé qui en explique
l'intention. Le message généré ne sert que de filet de sécurité.

### 3.2 Revoir, corriger, publier
```powershell
git log --oneline -15                 # historique local
git show HEAD                         # détail du dernier commit
git commit --amend                    # reformuler le dernier message (avant push)
git reset --soft HEAD~3               # regrouper les 3 derniers commits en un seul (avant push)
git push                              # publier, quand vous le décidez
```

### 3.3 Désactiver
- Pour une session : supprimer ou renommer `.claude/settings.json`, ou mettre
  `"disableAllHooks": true` dans `.claude/settings.local.json`.
- Pour les garde-fous Git : `git config --unset core.hooksPath` (déconseillé).

---

## 4. Déployer sur un serveur Ubuntu vierge

### 4.1 Prérequis
| Élément | Valeur |
|---|---|
| Système | Ubuntu 22.04 ou 24.04 (ou Debian 12), x86_64, avec un compte sudo |
| Ressources | 4 vCPU, 8 Go de RAM (16 Go conseillés), 30 Go de disque + les documents |
| Réseau | accès à GitHub (pour le clone), au registre Docker ou à un miroir interne (pour les images), et aux API internes des modèles |
| À connaître | URL et nom du LLM, URL, nom et dimension du modèle d'embeddings, reranker éventuel |

Rien d'autre n'est requis : Docker, Compose, git, curl, openssl et make sont installés par
`deploy.sh` s'ils manquent (seul `git` est nécessaire pour cloner, voir §4.2).

### 4.2 Récupérer le code
```bash
sudo apt-get update && sudo apt-get install -y git
```
Choisissez **un** mode d'accès au dépôt privé.

**a) Clé de déploiement SSH, en lecture seule (recommandé pour un serveur).**
Elle ne donne accès qu'à ce dépôt, et sans droit d'écriture :
```bash
ssh-keygen -t ed25519 -f ~/.ssh/contrats_rag_deploy -N "" -C "serveur-contrats-rag"
cat ~/.ssh/contrats_rag_deploy.pub
```
Sur GitHub : dépôt → **Settings → Deploy keys → Add deploy key**, collez la clé publique et
laissez « Allow write access » **décoché**. Puis :
```bash
cat >> ~/.ssh/config <<'EOF'
Host github-contrats
  HostName github.com
  User git
  IdentityFile ~/.ssh/contrats_rag_deploy
  IdentitiesOnly yes
EOF
sudo mkdir -p /opt/contrats-rag && sudo chown $USER: /opt/contrats-rag
git clone github-contrats:<organisation>/contrats-rag.git /opt/contrats-rag
```

**b) HTTPS avec un jeton fin (fine-grained token)** limité à ce dépôt, en lecture seule
(*Contents: Read-only*) :
```bash
sudo mkdir -p /opt/contrats-rag && sudo chown $USER: /opt/contrats-rag
git clone https://github.com/<organisation>/contrats-rag.git /opt/contrats-rag
# Identifiant : votre compte GitHub ; mot de passe : le jeton
```
Ne placez jamais le jeton dans l'URL ni dans un fichier du projet.

### 4.3 Déployer : une seule commande
```bash
cd /opt/contrats-rag
sudo ./deploy.sh
```
Le script affiche ses 5 étapes :

| Étape | Action |
|---|---|
| 1/5 Système | Vérifie l'OS, l'architecture, la RAM et le disque. Il avertit sans bloquer. |
| 2/5 Prérequis | Installe les éléments absents : Docker Engine (`docker.io`), Compose v2, git, curl, openssl, make. Active Docker au démarrage et ajoute votre compte au groupe `docker`. |
| 3/5 Configuration | Crée `.env` à partir de `.env.example`, génère les **5 secrets aléatoires** (droits `600`), puis **pose les questions** listées après ce tableau. |
| 4/5 Ressources | Crée les dossiers persistants (`DATA_ROOT/postgres`, `open-webui`, `contrats`, `inbox/`, `exports/`, `backups/`) et valide `compose.yml`. Le réseau Docker privé `<projet>_default` est créé au démarrage. PostgreSQL n'est joignable que par les conteneurs. |
| 5/5 Installation | Construit les images, démarre PostgreSQL (2 bases, 3 rôles), applique les migrations, démarre Open WebUI, puis contrôle chaque API de modèle. |

Questions de l'étape 3 (la touche Entrée garde la valeur proposée) :
```
URL du LLM (ex. http://llm.interne:8000/v1)   : http://10.20.30.40:8000/v1
Nom du modèle LLM (GET /v1/models)            : mistral-small
Clé API du LLM (vide si aucune)               : (saisie masquée)
URL des embeddings                            : http://10.20.30.41:8080/v1
Nom du modèle d'embeddings                    : BAAI/bge-m3
Dimension des embeddings                      : 1024
Reranker : cohere | tei | none                : none
Hôtes IA autorisés (liste blanche)            : 10.20.30.40,10.20.30.41   ← déduits des URL
Port de l'interface web                       : 3000
Dossier des données persistantes              : ./data   (ou /srv/contrats-rag/data)
Adresse de l'interface pour les utilisateurs  : http://<ip-du-serveur>:3000
```
Pour les réglages plus fins (préfixes d'embeddings, OCR, HTTPS, comptes), voir
[GUIDE_DEPLOIEMENT.md §6](GUIDE_DEPLOIEMENT.md). Après une modification de `.env` :
`make restart && make check`.

Fin attendue :
```
✅ Base contrats         PostgreSQL 17.x, pgvector 0.8.0
✅ LLM                   … → « OK »
✅ Embeddings            …, dimension 1024
 ✔  Déploiement terminé.
```
Après le déploiement, déconnectez-vous puis reconnectez-vous pour utiliser `docker` et
`make` sans `sudo`.

### 4.4 Variante non interactive (provisionnement automatisé)
Toutes les réponses peuvent venir de l'environnement :
```bash
sudo LLM_BASE_URL=http://10.20.30.40:8000/v1 LLM_MODEL=mistral-small \
     EMBEDDING_BASE_URL=http://10.20.30.41:8080/v1 EMBEDDING_MODEL=BAAI/bge-m3 EMBEDDING_DIM=1024 \
     RERANKER_API=none OWUI_PORT=3000 \
     ./deploy.sh --non-interactive
```
Autres variables acceptées : `AI_ALLOWED_HOSTS`, `DATA_ROOT`, `INBOX_DIR`, `WEBUI_URL`,
`RERANKER_BASE_URL`, `RERANKER_MODEL`, `EMBEDDING_QUERY_PREFIX` / `_DOCUMENT_PREFIX`
(entre guillemets s'ils contiennent un espace, ex. `'"query: "'`), `OCR_ENGINE`,
`DEFAULT_CONTRACT_ACCESS`, `ENABLE_SIGNUP`, `COMPOSE_PROJECT_NAME`, `APP_VERSION`.

Autres options :
- `./deploy.sh --env-file /chemin/.env` : reprend un `.env` déjà prêt, par exemple celui d'une
  sauvegarde (§6) ;
- `--skip-prereqs` : n'installe aucun paquet ;
- `--help` : affiche l'aide.

### 4.5 Mise en service (à faire une fois dans l'interface)
1. Ouvrir `http://<ip-du-serveur>:3000` et créer le **premier compte**, qui devient
   administrateur. Mettre ensuite `ENABLE_SIGNUP=false` dans `.env`, puis `make restart`.
2. Profil > Paramètres > Compte > **Clés API** : créer une clé, puis
   `make deploy-tools KEY=sk-…`. Le Tool et la Function filtre « RAG forcé » sont publiés.
3. Espace de travail > Modèles : créer **« Assistant Contrats »**.
   - modèle de base : le LLM interne ;
   - prompt : `owui_extensions/prompts/assistant_contrats_system.md` ;
   - **filtre « Contrats – Recherche systématique (RAG forcé) » coché** ;
   - outil « Contrats – Recherche et consultation ».
4. Documents : les déposer dans `inbox/<REFERENCE_CONTRAT>/`, puis `make ingest`,
   `make documents` et `make search Q="pénalités de retard"`.
5. Production : `DEFAULT_CONTRACT_ACCESS=none` et droits par groupe
   (`docker compose run --rm ingest access grant "group:Etudes"`).
6. Sécurité et sauvegardes : pare-feu, HTTPS et sauvegarde planifiée, voir
   [DEPLOIEMENT_UBUNTU.md §9-10](DEPLOIEMENT_UBUNTU.md).

---

## 5. Mettre à jour un serveur depuis GitHub
```bash
cd /opt/contrats-rag
./scripts/backup.sh /srv/sauvegardes        # sécurité avant mise à jour
git pull --ff-only
sudo ./deploy.sh                            # idempotent : .env et données conservés
```
`deploy.sh` ne remplace jamais un `.env` existant et ne supprime aucune donnée. Il
reconstruit les images, applique les nouvelles migrations et redémarre. Si `.env.example`
a gagné de nouvelles variables, comparez les deux fichiers :
```bash
diff <(grep -o '^[A-Z_]*=' .env.example | sort) <(grep -o '^[A-Z_]*=' .env | sort)
```

## 6. Migrer une installation existante avec ses données
Sur l'ancien serveur : `./scripts/backup.sh /srv/sauvegardes`, puis copier le dossier daté
(il contient les 2 bases, les fichiers et `env.backup`) vers le nouveau serveur, par un
canal interne, **jamais via GitHub**. Sur le nouveau serveur :
```bash
git clone github-contrats:<organisation>/contrats-rag.git /opt/contrats-rag && cd /opt/contrats-rag
sudo ./deploy.sh --env-file /srv/sauvegardes/AAAAMMJJ-HHMMSS/env.backup --non-interactive
./scripts/restore.sh /srv/sauvegardes/AAAAMMJJ-HHMMSS        # taper OUI
make check
```
Le même `.env` (mêmes mots de passe) est indispensable pour que les bases restaurées soient
accessibles.

## 7. Serveur sans accès à GitHub ni au registre Docker
Sur une machine connectée : `git clone …`, puis `make bundle`. Cela produit
`dist/contrats-rag-<version>.tar.gz` (≈ 1,8 Go), qui contient les images Docker et
`deploy.sh`. Copiez-le sur le serveur, puis :
```bash
tar -xzf contrats-rag-0.1.0.tar.gz -C /opt && cd /opt/contrats-rag-0.1.0
sudo ./deploy.sh --skip-prereqs        # Docker installé depuis le miroir interne
```
Les images sont chargées depuis `images/` au lieu d'être construites. Voir
[DEPLOIEMENT_UBUNTU.md §12](DEPLOIEMENT_UBUNTU.md).

---

## 8. Dépannage

| Symptôme | Solution |
|---|---|
| `Permission denied (publickey)` au clone | clé de déploiement absente ou mauvais alias : `ssh -T github-contrats` |
| `Repository not found` | dépôt privé : vérifier la clé de déploiement ou les droits du jeton |
| `./deploy.sh: Permission denied` | `bash deploy.sh` (droit d'exécution perdu lors de la copie) |
| `Compose v2 introuvable` | dépôt Docker officiel ou miroir interne : paquet `docker-compose-plugin` |
| Build : `i/o timeout`, `network is unreachable` | réseau ou proxy : relancer `sudo ./deploy.sh` (5 essais par étape). Derrière un proxy, configurer le proxy du démon Docker. Sinon, §7 |
| `❌ … n'est pas dans CRAG_ALLOWED_HOSTS` | ajouter l'hôte dans `AI_ALLOWED_HOSTS` de `.env`, puis `make restart` |
| `❌ Embeddings … Dimension` | corriger `EMBEDDING_DIM`, puis `docker compose run --rm ingest reindex --all` |
| `docker: permission denied` après le déploiement | se déconnecter et se reconnecter (groupe `docker`) |
| Commit refusé « BLOQUÉ » | un secret ou un document est indexé : `git restore --staged <fichier>`, déplacer la valeur dans `.env` |
| Push refusé « contenu sensible » | un commit déjà fait contient un secret : le retirer de l'historique local (`git reset --soft`, `git commit --amend`) avant de repousser. Si un secret a déjà été publié, le **révoquer** et le régénérer. |
