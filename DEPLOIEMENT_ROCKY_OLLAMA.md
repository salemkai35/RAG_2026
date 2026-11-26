# Déploiement sur deux machines : Rocky Linux (application) + Windows (Ollama)

Ce document adapte Contrats RAG à la topologie cible :

| Machine | IP | Rôle |
|---|---|---|
| **Rocky Linux 10.1**, 4 vCPU, 3,6 Gio RAM, 3,9 Gio swap, 70 Go `/` | `172.21.99.93` | Open WebUI, PostgreSQL + pgvector, logique RAG (`contract_rag`), passerelle OCR. **Aucun modèle d'IA.** |
| **Station Windows + Ollama** | `172.21.36.55` | Seul serveur d'IA, `http://172.21.36.55:11434` : Qwen 27B Q4 (LLM), Qwen3-Embedding-8B (embeddings), OCRia 1.5 4B (OCR / vision) |

Il contient l'architecture retenue, l'étude de l'intégration d'OCRia (avec la réponse à la
question « faut-il un service FastAPI ? »), les réglages des deux machines et les étapes de
validation.

> Prérequis de lecture : [DEPLOIEMENT_GIT.md](DEPLOIEMENT_GIT.md), pour le clone et
> `deploy.sh`. Les sections ci-dessous ne décrivent que ce qui change pour cette topologie.

---

## 1. Synthèse

1. **Une seule connexion de modèles** : Ollama expose une API compatible OpenAI sur
   `http://172.21.36.55:11434/v1`. Le LLM, les embeddings et l'OCR passent tous par cette
   adresse ; seul le nom du modèle change. Rien n'est installé sur Rocky pour l'IA.
2. **Qwen3-Embedding-8B produit des vecteurs de dimension 4096**, alors que les index pgvector
   sont limités à 2000 dimensions (4000 en `halfvec`). On demande donc à Ollama **1024
   dimensions**, grâce au paramètre `dimensions` (le modèle est entraîné en « Matryoshka »),
   avec une troncature de secours côté client. Fonction ajoutée au projet et testée.
3. **OCRia dans l'ingestion des contrats : aucun service intermédiaire n'est nécessaire.**
   L'ingestion découpe déjà chaque page PDF sans texte en image et l'envoie au modèle Vision
   (`OCR_ENGINE=vision`).
4. **OCRia pour les fichiers déposés dans le chat d'Open WebUI : un petit service est
   nécessaire**, car Open WebUI ne sait pas transformer un PDF en images pour Ollama. Il est
   livré sous le nom `ocr-bridge`. C'est un **service Python sans FastAPI** (bibliothèque
   standard), qui réutilise le code de l'ingestion, n'est publié sur aucun port de l'hôte et
   s'active par `COMPOSE_PROFILES=ocr` (§4).
5. **Point dur : 3,6 Gio de RAM sur Rocky.** Cela suffit pour quelques utilisateurs
   simultanés, mais **8 Go sont recommandés** (§3.4). Côté Windows, la **mémoire vidéo**
   détermine si les trois modèles restent chargés ensemble (§3.3).

---

## 2. Architecture

```
 Postes utilisateurs ── navigateur ──► http://172.21.99.93:3000
┌──────────────────────── Rocky Linux 172.21.99.93 (Docker) ───────────────────────────┐
│                                                                                      │
│  open-webui (v0.13.4 + contract_rag)                                                 │
│   ├─ Modèle « Assistant Contrats » = Qwen 27B + filtre « RAG forcé » + Tool          │
│   │     └─ recherche hybride ──────────────────────────────┐                          │
│   └─ Fichier déposé dans le chat ─► ocr-bridge :8090 ─┐    │  (réseau Docker interne)│
│                                     (profil « ocr »)  │    │                          │
│  ingest (CLI ponctuelle) : PDF/Word/Excel → chunks ───┼────┤                          │
│                                                       │    ▼                          │
│                                       postgres 17 + pgvector                          │
│                                        ├─ base openwebui (comptes, conversations)    │
│                                        └─ base contrats  (chunks, vector(1024))      │
└───────────────────────────────────────────────────────┼──────────────────────────────┘
                     HTTP, LAN interne uniquement       │  (liste blanche AI_ALLOWED_HOSTS)
                                                        ▼
┌──────────────────────── Windows 172.21.36.55 ───────────────────────────────────────┐
│  Ollama :11434  (pare-feu : n'accepte que 172.21.99.93)                              │
│   ├─ Qwen 27B Q4          → /v1/chat/completions  (conversation, raisonnement)       │
│   ├─ Qwen3-Embedding-8B   → /v1/embeddings        (dimensions = 1024)                │
│   └─ OCRia 1.5 4B         → /v1/chat/completions + image  (OCR des pages)            │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 Les trois flux

| Flux | Chemin | Modèles appelés |
|---|---|---|
| **Question sur les contrats** | Open WebUI → filtre « RAG forcé » → `contract_rag` : la question est vectorisée, puis la recherche hybride interroge PostgreSQL → Open WebUI envoie au LLM la question et les passages | Embeddings (1 appel), puis Qwen 27B |
| **Ingestion d'un contrat** | `make ingest` → lecture PDF/Word/Excel → pages sans texte rendues en image → OCRia → découpage par article → embeddings → PostgreSQL | OCRia (une fois par page scannée), puis Embeddings |
| **Fichier déposé dans le chat** | Open WebUI → `ocr-bridge` (même lecteur que l'ingestion, OCRia pour les scans) → texte complet donné au LLM | OCRia, puis Qwen 27B |

### 2.2 Ce qui ne change pas
Open WebUI reste épinglé (v0.13.4) et non modifié. Les deux bases restent séparées, et les
droits métier, les citations et le garde-fou des montants sont inchangés. Le serveur Rocky
n'a besoin d'Internet que pour installer Docker et construire les images, ce qu'on peut
éviter avec le paquet hors ligne (§5.6).

---

## 3. Étude et points de vigilance

### 3.1 Embeddings : Qwen3-Embedding-8B et pgvector
| Constat | Conséquence | Réglage retenu |
|---|---|---|
| Vecteurs natifs de 4096 dimensions | dépasse la limite des index HNSW (2000 en `vector`, 4000 en `halfvec`) | `EMBEDDING_DIM=1024` |
| Modèle entraîné en Matryoshka (MRL) : les premières composantes portent l'essentiel du sens | réduire à 1024 garde une très bonne qualité, avec 4 fois moins de stockage et de RAM | `EMBEDDING_REQUEST_DIMENSIONS=true` : Ollama renvoie directement 1024 valeurs (testé avec Ollama 0.40 dans WSL : 768 → 512) |
| Une version d'Ollama qui ignorerait `dimensions` | les vecteurs reviendraient en 4096 | `EMBEDDING_TRUNCATE=true` : troncature et renormalisation côté client (testé) |
| Qwen3-Embedding attend une **instruction** pour les requêtes, pas pour les documents | sans elle, la recherche est moins pertinente | `EMBEDDING_QUERY_PREFIX="Instruct: …\nQuery: "` et `EMBEDDING_DOCUMENT_PREFIX=` vide |
| Open WebUI, pour son RAG natif (fichiers du chat), **n'envoie pas** `dimensions` (vérifié dans le code v0.13.4) | il stockerait des vecteurs de 4096 dimensions, ce qui échouerait sur l'index | `OWUI_BYPASS_EMBEDDING=true` : les fichiers du chat sont passés en entier au LLM, sans vectorisation. Les contrats, eux, passent par `contract_rag`. |

> **La dimension est figée à la première ingestion.** La changer ensuite oblige à lancer
> `reindex --all`.

### 3.2 Rocky Linux 10
| Sujet | Traitement |
|---|---|
| Docker | Rocky fournit Podman, que les scripts ne prennent pas en charge (ils utilisent Docker Compose v2). `deploy.sh` installe **Docker CE** depuis le dépôt Docker pour RHEL et remplace Podman (`--allowerasing`). Sans accès Internet : `DOCKER_REPO_URL=<dépôt du miroir interne>`. **À vérifier** : un dépôt Docker CE doit exister pour EL10 (sur download.docker.com ou sur le miroir interne). |
| SELinux (Enforcing par défaut) | `deploy.sh` donne le type `container_file_t` aux dossiers montés (`data/`, `inbox/`, `exports/`, `backups/`), sinon PostgreSQL ne peut pas écrire |
| firewalld | `deploy.sh` ouvre uniquement le port de l'interface (3000/tcp). PostgreSQL et `ocr-bridge` ne sont publiés sur aucun port. |
| `/home` séparé (45 Go) | installer dans `/opt/contrats-rag` (sur `/`, 70 Go). Les données vont dans `DATA_ROOT`, sur le volume le plus grand. |

### 3.3 Un seul serveur Ollama pour trois modèles
| Sujet | Risque | Mesure |
|---|---|---|
| **Mémoire vidéo** (valeurs indicatives, à mesurer avec `ollama ps`) : Qwen 27B Q4 ≈ 16 à 18 Go, Embedding 8B ≈ 5 Go (Q4) à 16 Go (F16), OCRia 4B ≈ 3 à 5 Go, plus le contexte | si les trois ne tiennent pas ensemble, Ollama décharge et recharge un modèle à chaque changement : 10 à 60 s de latence | `OLLAMA_MAX_LOADED_MODELS=3` si la VRAM le permet (≈ 26 Go et plus). Sinon, garder 2 modèles et lancer l'ingestion (OCR + embeddings) hors des heures d'usage. |
| **Longueur de contexte** : le RAG forcé injecte ≈ 8 passages de 1800 caractères, soit ≈ 4 à 5 000 jetons, plus le prompt et l'historique | une fenêtre trop courte coupe le contexte **sans erreur**, et la réponse est fausse ou incomplète | `OLLAMA_CONTEXT_LENGTH=16384` sur Windows, et vérifier avec `ollama ps` |
| **Modèle à raisonnement** (Qwen 3.x « thinking ») | réponses lentes, et `check` peut recevoir une réponse vide | désactiver le raisonnement pour l'assistant si le modèle le permet (paramètres avancés du modèle dans Open WebUI). `check` signale désormais une réponse vide. |
| **Requêtes simultanées** | plusieurs utilisateurs plus une ingestion : file d'attente | `OLLAMA_NUM_PARALLEL=2` ou plus (consomme de la VRAM), à mesurer |
| **Disponibilité de la station Windows** (veille, mises à jour, redémarrage) | plus d'IA, alors qu'Open WebUI répond toujours | désactiver la veille ; Ollama au démarrage ; créneau de redémarrage planifié. L'ingestion supporte une coupure : statut `a_vectoriser`, puis `make reindex`. |
| **Sécurité** : Ollama n'a **ni authentification ni TLS** | toute machine qui joint le port 11434 peut utiliser les modèles, et les textes des contrats circulent en clair sur le LAN | pare-feu Windows limité à `172.21.99.93` (§5.1) ; `AI_ALLOWED_HOSTS=172.21.36.55` côté Rocky. Si le réseau n'est pas de confiance, mettre un proxy TLS devant Ollama. |

### 3.4 Mémoire de Rocky (3,6 Gio)
Estimation à confirmer par `docker stats` (§6, étape V10) :

| Composant | Mémoire habituelle |
|---|---|
| Open WebUI (sans modèle local : embeddings et LLM distants) | ≈ 0,8 à 1,3 Go |
| PostgreSQL (≈ 200 contrats, vecteurs de 1024 dimensions) | ≈ 0,2 à 0,5 Go |
| `ocr-bridge` (au repos / pendant un OCR) | ≈ 0,1 / 0,4 Go |
| `ingest` (ponctuel : rendu des pages à 300 dpi) | ≈ 0,3 à 0,8 Go |
| Système Rocky | ≈ 0,5 Go |

Cela passe avec le swap de 3,9 Gio pour une petite équipe. Pour fiabiliser :
- lancer l'ingestion hors des heures d'usage ;
- garder `VISION_MAX_SIDE=1800` ;
- passer la VM à **8 Go**, ce qui supprime le risque.

La **construction** des images sur le serveur demande aussi de la mémoire. Si elle échoue,
construire sur une autre machine et livrer le paquet hors ligne (§5.6).

---

## 4. Étude : intégrer OCRia et faut-il un service FastAPI ?

### 4.1 Ce qu'Ollama sait faire
OCRia est un modèle **vision-langage** : il reçoit une **image** et renvoie du texte. Ollama
l'expose comme un modèle de chat (`/v1/chat/completions` avec un `image_url` en base64).
Ollama **n'accepte pas de PDF** : il faut d'abord rendre chaque page en image.

### 4.2 Les trois cas d'usage
| Cas | Faut-il un service intermédiaire ? | Solution |
|---|---|---|
| **A. Ingestion des contrats** (le cœur du projet) | **Non** | Déjà en place : `pdfplumber` détecte les pages sans texte, `pypdfium2` les rend en image, et `VisionOcr` les envoie à OCRia. Chaque page garde son numéro et sa méthode (`ocr:vision`) pour la traçabilité. Réglage : `OCR_ENGINE=vision`, `VISION_MODEL=<ocria>`. |
| **B. Image déposée dans le chat** (photo, capture d'écran) | **Non** | Choisir le modèle OCRia dans Open WebUI et joindre l'image : Open WebUI transmet nativement les images aux modèles vision d'Ollama. |
| **C. PDF scanné déposé dans le chat** | **Oui** | Open WebUI v0.13.4 extrait le texte des PDF avec ses lecteurs internes, qui ne font pas d'OCR. Ses moteurs OCR intégrés (Tika, Docling, Mistral OCR en ligne, PaddleOCR-VL, MinerU) exigent tous **leur propre serveur**, et aucun ne sait appeler un modèle vision d'Ollama. Seul le moteur **« External »** permet de brancher notre propre extraction. |

### 4.3 Le service retenu : `ocr-bridge` (sans FastAPI)
Le moteur « External » d'Open WebUI attend **un seul point d'entrée**. Ce contrat a été
vérifié dans le code v0.13.4 :
```
PUT http://ocr-bridge:8090/process
  corps   : octets du fichier
  en-têtes: Content-Type, X-Filename, Authorization: Bearer <OCR_BRIDGE_API_KEY>
réponse   : [{"page_content": "…", "metadata": {"page": 0, "source": "…"}}, …]
```

| Critère | FastAPI | `ocr-bridge`, bibliothèque standard (retenu) |
|---|---|---|
| Points d'entrée nécessaires | 1 (+ `/health`) | 1 (+ `/health`) |
| Dépendances nouvelles | fastapi, uvicorn, pydantic et leur maintenance | **aucune** |
| Code métier | à brancher sur `contract_rag` | **réutilise** les lecteurs et l'OCR de l'ingestion : un PDF du chat est lu exactement comme un contrat |
| Image et mémoire | nouvelle image ou dépendances supplémentaires | **même image** que `ingest`, ≈ 100 Mo de RAM au repos |
| Décision d'architecture (pas de FastAPI en V1) | remise en cause | respectée |

FastAPI deviendra pertinent avec plusieurs points d'entrée et des traitements asynchrones,
comme l'interface de validation des extractions ou des files de traitement prévues en
phase 4. `ocr-bridge` pourra alors y être intégré.

Formats traités par la passerelle :

| Format | Traitement |
|---|---|
| PDF | texte natif, et OCRia pour les pages sans texte ; un document par page |
| DOCX, XLSX | lecteurs existants, références de cellules comprises |
| Images (PNG, JPG, TIFF multipage, WEBP) | OCRia |
| Texte (TXT, MD, CSV, JSON) | transmis tel quel |
| Autres formats | refusés (`415`), avec un message clair |

Sécurité de la passerelle :
- aucun port publié sur l'hôte : seul Open WebUI la joint, par le réseau Docker interne ;
- elle exige une clé (générée par `install.sh`) ;
- elle refuse les fichiers de plus de 100 Mo (`CRAG_BRIDGE_MAX_MB`) ;
- elle n'écrit rien sur disque en dehors d'un dossier temporaire supprimé après chaque
  requête.

**Limite** : un PDF de 50 pages scannées, c'est 50 appels à OCRia, soit plusieurs minutes
pendant lesquelles l'utilisateur attend dans le chat. Les gros dossiers passent par
l'ingestion (cas A), pas par le chat.

---

## 5. Configuration des deux machines

### 5.1 Station Windows (Ollama), en PowerShell **administrateur**
```powershell
# Variables d'Ollama, pour toute la machine (redémarrer Ollama ensuite)
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "Machine")
[Environment]::SetEnvironmentVariable("OLLAMA_CONTEXT_LENGTH", "16384", "Machine")
[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "30m", "Machine")
[Environment]::SetEnvironmentVariable("OLLAMA_MAX_LOADED_MODELS", "3", "Machine")   # 2 si la VRAM est insuffisante
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", "2", "Machine")
```
```powershell
# Pare-feu : port 11434 ouvert UNIQUEMENT au serveur Rocky
New-NetFirewallRule -DisplayName "Ollama - Contrats RAG (Rocky)" -Direction Inbound -Protocol TCP -LocalPort 11434 -RemoteAddress 172.21.99.93 -Action Allow -Profile Domain,Private
```
Supprimez aussi toute règle plus large créée par l'installeur d'Ollama : à vérifier dans
« Pare-feu Windows Defender avec fonctions avancées de sécurité ».

Ensuite :
- quitter Ollama (icône de la barre des tâches), puis le relancer ;
- vérifier qu'il démarre à l'ouverture de session, ou l'installer en service ;
- désactiver la mise en veille : Paramètres → Système → Alimentation.

Relever les **noms exacts** des modèles, qui serviront dans `.env` :
```powershell
ollama list
```
Le modèle d'embeddings doit être chargeable et renvoyer **1024** valeurs. Ce test
s'exécute sur la station Windows elle-même :
```powershell
$b = @{ model = "qwen3-embedding:8b"; input = @("test"); dimensions = 1024 } | ConvertTo-Json
(Invoke-RestMethod -Uri http://localhost:11434/v1/embeddings -Method Post -Body $b -ContentType "application/json").data[0].embedding.Count
```
Remplacez `qwen3-embedding:8b` par le nom lu dans `ollama list`.

### 5.2 Serveur Rocky : préparation
```bash
sudo dnf -y update && sudo dnf -y install git curl
# L'API Ollama est-elle joignable depuis Rocky ?
curl -s http://172.21.36.55:11434/api/version
curl -s http://172.21.36.55:11434/api/tags | python3 -m json.tool | grep '"name"'
```
Si ces commandes échouent, ne pas aller plus loin. Vérifier le pare-feu Windows,
`OLLAMA_HOST` et le routage entre `172.21.99.0` et `172.21.36.0`.

### 5.3 Serveur Rocky : cloner et déployer (deux commandes)
```bash
sudo git clone https://github.com/salemkai35/RAG_2026.git /opt/contrats-rag && cd /opt/contrats-rag
sudo ./deploy.sh
```
Le dépôt est privé : `git clone` demande l'identifiant GitHub et, comme mot de passe, un
jeton en lecture seule. On peut aussi utiliser une clé de déploiement (DEPLOIEMENT_GIT.md §4.2).

Déroulé à l'écran :
```
Serveur Ollama (vide = autre API compatible OpenAI) [http://172.21.36.55:11434] :   ← Entrée
==> Détection des modèles sur http://172.21.36.55:11434…
 ✔  Ollama 0.x : <les modèles présents>
     LLM        : <Qwen 27B>
     Embeddings : <Qwen3-Embedding 8B> (dimension native 4096 → 1024)
     OCR        : <OCRia>
    Utiliser cette configuration ? [O/n]                                              ← Entrée
Hôtes IA autorisés … [172.21.36.55] · Port [3000] · Données [./data] · Adresse [http://172.21.99.93:3000]   ← Entrée
```
L'adresse d'Ollama vient de `config/site.env`, qui est versionné. Pour un autre site,
modifiez ce fichier, ou lancez `sudo OLLAMA_URL=http://<ip>:11434 ./deploy.sh`. Sans aucune
question : `sudo ./deploy.sh --non-interactive`.

Choix automatiques (affichés, puis modifiables dans `.env`) :

| Rôle | Règle de choix |
|---|---|
| LLM | modèle Qwen le plus gros, hors embeddings et OCR |
| Embeddings | nom contenant « embed » ; dimension mesurée par un appel réel. Au-delà de 2000, 1024 est demandé à Ollama, avec troncature de secours, préfixe d'instruction Qwen3 et bypass Open WebUI. |
| OCR | nom contenant « ocr » (sinon « vl » ou « vision »). Active `OCR_ENGINE=vision` et la passerelle `ocr-bridge`. |

Si Ollama est injoignable, `deploy.sh` le dit et passe à la saisie manuelle des modèles.

`deploy.sh` enchaîne alors :
1. famille RHEL détectée, puis Docker CE installé et activé ;
2. `.env` créé avec ses 6 secrets (dont la clé de la passerelle) ;
3. SELinux et firewalld configurés ;
4. images construites, puis PostgreSQL, migrations (`vector(1024)`), Open WebUI et `ocr-bridge` démarrés ;
5. contrôle des 3 modèles.

### 5.4 Récapitulatif de `.env` (partie modèles et Open WebUI)
```env
LLM_BASE_URL=http://172.21.36.55:11434/v1
LLM_MODEL=<nom exact du Qwen 27B>
EMBEDDING_BASE_URL=http://172.21.36.55:11434/v1
EMBEDDING_MODEL=<nom exact du Qwen3-Embedding 8B>
EMBEDDING_DIM=1024
EMBEDDING_REQUEST_DIMENSIONS=true
EMBEDDING_TRUNCATE=true
EMBEDDING_QUERY_PREFIX="Instruct: Given a question about a public works contract, retrieve the contract passages that answer it\nQuery: "
EMBEDDING_DOCUMENT_PREFIX=
RERANKER_API=none
OCR_ENGINE=vision
VISION_BASE_URL=http://172.21.36.55:11434/v1
VISION_MODEL=<nom exact d'OCRia>
VISION_MAX_SIDE=1800
AI_ALLOWED_HOSTS=172.21.36.55
WEBUI_URL=http://172.21.99.93:3000
CORS_ALLOW_ORIGIN=http://172.21.99.93:3000
COMPOSE_PROFILES=ocr
OWUI_CONTENT_EXTRACTION_ENGINE=external
OWUI_EXTERNAL_LOADER_URL=http://ocr-bridge:8090
OWUI_BYPASS_EMBEDDING=true
```
Pour Open WebUI, `compose.yml` pointe automatiquement la connexion « OpenAI » sur
`LLM_BASE_URL`. Open WebUI liste alors **tous** les modèles d'Ollama, et ses embeddings natifs
utilisent `EMBEDDING_*`.

### 5.5 Dans Open WebUI (une fois, en administrateur)
1. Créer le compte administrateur, puis mettre `ENABLE_SIGNUP=false` et lancer `make restart`.
2. **Panneau d'administration → Paramètres → Documents** : vérifier que le moteur d'extraction
   est `External`, avec l'URL `http://ocr-bridge:8090` et une clé renseignée, et que l'option
   de contournement de la vectorisation (« Bypass Embedding and Retrieval ») est activée.
   Ces valeurs viennent de `.env` **au premier démarrage seulement**. Ensuite, c'est l'écran
   d'administration qui fait foi.
3. **Panneau d'administration → Paramètres → Modèles** : masquer aux utilisateurs le modèle
   d'embeddings, qui ne sait pas converser.
4. `make deploy-tools KEY=sk-…`, puis créer **« Assistant Contrats »** :
   - modèle de base : Qwen 27B ;
   - filtre **« RAG forcé »** coché, outil coché ;
   - prompt système du projet ;
   - désactiver le raisonnement si Qwen est en mode « thinking ».
5. Optionnel : un modèle **« Lecture de documents (OCR) »** basé sur OCRia, pour poser des
   questions sur une image jointe (cas B du §4.2).

### 5.6 Sans Internet sur Rocky
Sur une machine connectée : `make bundle`, qui produit `dist/contrats-rag-<version>.tar.gz`.
Copier ce paquet sur Rocky, installer Docker CE depuis le miroir interne, puis lancer
`sudo ./deploy.sh --skip-prereqs` avec les mêmes variables qu'au §5.3.

---

## 6. Étapes de validation

À dérouler dans l'ordre. Chaque étape a un résultat attendu ; ne passez à la suivante
qu'une fois l'étape réussie.

| # | Où | Commande / action | Résultat attendu |
|---|---|---|---|
| V1 | Windows | `ollama list` ; `ollama ps` après un premier appel | les 3 modèles sont présents ; mémoire et contexte (16384) visibles |
| V2 | Rocky | `curl -s http://172.21.36.55:11434/api/version` | version renvoyée, en moins d'une seconde |
| V3 | autre poste du LAN | `curl -m 5 http://172.21.36.55:11434/api/version` | **échec** (pare-feu) : seul Rocky accède à Ollama |
| V4 | Rocky | `cd /opt/contrats-rag && make check` | ✅ base (pgvector 0.8), ✅ LLM « OK », ✅ embeddings **dimension 1024**, ✅ OCR « Vision … → lecture exacte » |
| V5 | Rocky | `docker compose ps` ; `sudo ss -ltnp \| grep -E '3000\|5432\|8090'` | `postgres`, `open-webui` et `ocr-bridge` sont `healthy` ; **seul le port 3000** est publié |
| V6 | Rocky | `getenforce` ; `sudo ausearch -m avc -ts recent \| tail` | `Enforcing`, sans refus AVC lié aux conteneurs |
| V7 | Rocky | déposer un contrat PDF **scanné** dans `inbox/TEST-001/`, puis `make ingest` et `make documents` | `make ingest` affiche ✅ avec le nombre de chunks ; `make documents` indique `indexe`. Comparer le texte trouvé par `make search` au document papier pour juger la qualité de l'OCR. |
| V8 | Rocky | `make search Q="pénalités de retard"` | l'article attendu, avec fichier et page |
| V9 | navigateur | Assistant Contrats : « Quel est le taux des pénalités de retard du contrat TEST-001 ? » | réponse exacte et citée ; temps de réponse noté |
| V10 | navigateur | déposer un PDF scanné dans une conversation, puis poser une question dessus | texte lu par OCRia (`docker compose logs ocr-bridge` : « n page(s) extraites ») et réponse correcte |
| V11 | Rocky | `docker stats --no-stream` pendant V9 et V10 ; `free -h` | Open WebUI < 1,5 Go ; swap peu sollicité. Sinon, passer à 8 Go. |
| V12 | Windows puis Rocky | arrêter Ollama, ingérer un fichier, redémarrer Ollama, puis `make reindex` | statut `a_vectoriser`, puis `indexe` : aucune perte |
| V13 | les deux | redémarrer Windows puis Rocky | tout repart seul ; `make check` vert |
| V14 | Rocky | `./scripts/backup.sh /srv/sauvegardes` | dossier daté avec les 2 dumps et `SHA256SUMS` |

### 6.1 Ce qui a déjà été validé (WSL Ubuntu, 26/11/2026)
| Validé | Non validé ici (à faire sur site) |
|---|---|
| Paramètre `dimensions` d'Ollama sur `/v1/embeddings` (768 → 512), colonne `vector(512)` créée, recherche fonctionnelle | Qwen3-Embedding-8B réel (dimension 1024 demandée) |
| Troncature et renormalisation côté client (tests unitaires) | OCRia réel : qualité de lecture, durée par page, taille d'image optimale |
| `ocr-bridge` : santé, clé, refus d'un format inconnu, PDF scanné → OCR → texte, appel **depuis le conteneur Open WebUI** par le réseau Docker | même chose avec `OCR_ENGINE=vision` (testé ici avec Tesseract) |
| Open WebUI reçoit `CONTENT_EXTRACTION_ENGINE=external`, l'URL, la clé et le bypass | dépôt d'un fichier dans le chat depuis l'interface (V10) |
| Ingestion d'un PDF scanné → recherche → passage retrouvé | Qwen 27B : contexte de 16k, mode « thinking », temps de réponse |
| `deploy.sh` (chemin Debian), idempotence, 83 tests unitaires | chemin **Rocky** de `deploy.sh` : dnf, Docker CE EL10, SELinux, firewalld |

---

## 7. Dépannage spécifique

| Symptôme | Cause probable | Solution |
|---|---|---|
| `check` : `ConnectTimeout` vers 172.21.36.55 | pare-feu Windows, `OLLAMA_HOST` non appliqué, ou routage | V2 depuis Rocky ; sur Windows, `netstat -ano \| findstr 11434` doit montrer `0.0.0.0:11434` |
| `Dimension d'embedding 4096 ≠ 1024` | `EMBEDDING_REQUEST_DIMENSIONS` ou `EMBEDDING_TRUNCATE` absent de `.env` | les ajouter, puis `make restart && make check` |
| Changement de `.env` sans effet sur la passerelle | `make restart` ne recrée qu'Open WebUI | `docker compose up -d --force-recreate ocr-bridge` |
| `LLM … a répondu vide` | modèle à raisonnement : tous les jetons sont partis dans le raisonnement | désactiver le raisonnement, ou utiliser une variante sans « thinking » |
| Réponses qui ignorent les passages | contexte Ollama trop court | `OLLAMA_CONTEXT_LENGTH=16384` puis redémarrer Ollama ; contrôler avec `ollama ps` |
| Première réponse très lente, puis normale | chargement du modèle en VRAM | `OLLAMA_KEEP_ALIVE=30m` ; VRAM suffisante pour `OLLAMA_MAX_LOADED_MODELS` |
| OCR : `[illisible]` partout ou texte inventé | image trop réduite, ou modèle inadapté | augmenter `VISION_MAX_SIDE` (2200 à 2500), puis `make ingest` du fichier avec `--force` ; comparer avec `OCR_ENGINE=tesseract` |
| Dépôt dans le chat : « Error loading document » | passerelle arrêtée, clé différente, ou réglage d'administration modifié | `docker compose ps ocr-bridge`, `docker compose logs ocr-bridge`, puis l'écran Documents (§5.5) |
| PostgreSQL : `Permission denied` sur `/var/lib/postgresql/data` | étiquette SELinux absente | `sudo restorecon -Rv /opt/contrats-rag/data`, ou relancer `sudo ./deploy.sh` |
| `dnf` : conflit `podman`, `runc` ou `containerd` | Podman préinstallé | déjà traité par `--allowerasing` ; sinon `sudo dnf remove podman buildah` |
| Conteneurs tués (`OOMKilled`) | RAM de 3,6 Gio dépassée | ingestion hors des heures d'usage, `VISION_MAX_SIDE` plus bas, ou VM à 8 Go |
