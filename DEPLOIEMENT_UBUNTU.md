# Copier le projet et le déployer sur un serveur Ubuntu – procédure détaillée

Ce document décrit pas à pas comment **copier entièrement le projet Contrats RAG** depuis le
poste Windows et le **déployer sur un serveur Ubuntu** (22.04 ou 24.04) avec Docker, avec
toute sa configuration.

Chaque étape donne la commande exacte, le résultat attendu et quoi faire si ça échoue.
Cette procédure a été exécutée avec succès sur Ubuntu 24.04 (voir GUIDE_DEPLOIEMENT.md §12).

> Documents complémentaires : [ARCHITECTURE.md](ARCHITECTURE.md) (fonctionnement),
> [GUIDE_DEPLOIEMENT.md](GUIDE_DEPLOIEMENT.md) (référence complète de `.env` et dépannage),
> [docs/CONNEXION_MODELES.md](docs/CONNEXION_MODELES.md) (exemples d'API de modèles).

---

## Sommaire

0. [Choisir son cas](#0-choisir-son-cas)
1. [Préparer le serveur Ubuntu](#1-préparer-le-serveur-ubuntu)
2. [Préparer la copie sur le poste Windows](#2-préparer-la-copie-sur-le-poste-windows)
3. [Transférer la copie vers le serveur](#3-transférer-la-copie-vers-le-serveur)
4. [Installer le projet sur le serveur](#4-installer-le-projet-sur-le-serveur)
5. [Configurer `.env` (modèles, sécurité, stockage)](#5-configurer-env)
6. [Lancer l'installation et vérifier](#6-lancer-linstallation-et-vérifier)
7. [Mettre en service dans Open WebUI](#7-mettre-en-service-dans-open-webui)
8. [Charger les contrats](#8-charger-les-contrats)
9. [Sécuriser le serveur](#9-sécuriser-le-serveur)
10. [Sauvegardes automatiques et redémarrage](#10-sauvegardes-automatiques-et-redémarrage)
11. [Cas particulier : migrer avec les données](#11-cas-particulier--migrer-une-installation-avec-ses-données)
12. [Cas particulier : serveur sans Internet](#12-cas-particulier--serveur-sans-internet)
13. [Liste de contrôle finale](#13-liste-de-contrôle-finale)
14. [Dépannage rapide](#14-dépannage-rapide)

---

## 0. Choisir son cas

| Votre situation | Suivez |
|---|---|
| Nouveau serveur **avec** accès Internet (ou miroir Docker interne) | §1 → §10 |
| Nouveau serveur **sans** Internet | §1, puis §12 à la place des §2 à §4, puis §5 → §10 |
| Déplacer une installation existante **avec ses contrats, comptes et conversations** | §1 → §4, puis §11 |

Ce qui est copié et ce qui ne l'est pas :

| Élément | Copié ? | Pourquoi |
|---|---|---|
| Code, `compose.yml`, `docker/`, `scripts/`, `owui_extensions/`, `docs/` | ✅ oui | c'est le projet |
| `.env.example` | ✅ oui | modèle de configuration |
| `.env` | ❌ non (sauf migration §11) | contient les secrets ; il est régénéré sur le serveur |
| `data/` | ❌ non (sauf migration §11) | bases et fichiers de l'ancienne installation |
| `.venv/`, `__pycache__/`, `dist/`, `backups/` | ❌ jamais | propres au poste Windows ou volumineux |
| Les modèles d'IA | ❌ non | ils restent sur les serveurs d'API internes |

---

## 1. Préparer le serveur Ubuntu

### 1.1 Prérequis

| Ressource | Minimum | Conseillé |
|---|---|---|
| Système | Ubuntu 22.04 ou 24.04, x86_64 | Ubuntu 24.04 LTS |
| CPU | 4 vCPU | 8 vCPU |
| RAM | 8 Go | 16 Go |
| Disque | 30 Go + volume des documents | volume dédié, ex. `/srv` |
| Réseau | accès aux API internes des modèles (LLM, embeddings…) | |
| Port entrant | `3000/tcp` (interface web) ou `443` derrière un proxy | |

Vérifier le système :
```bash
lsb_release -a          # Ubuntu 22.04 / 24.04
uname -m                # x86_64
free -h                 # RAM
df -h /opt /srv         # espace disque
```

### 1.2 Mettre à jour le système
```bash
sudo apt-get update
sudo apt-get upgrade -y
sudo apt-get install -y ca-certificates curl tar make nano
```

### 1.3 Installer Docker et Docker Compose v2

**Option 1 – paquets Ubuntu (méthode testée, la plus simple) :**
```bash
sudo apt-get install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
```

**Option 2 – dépôt officiel Docker (versions plus récentes) :**
```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
```

**Option 3 – serveur sans Internet** : installer Docker depuis le miroir interne de paquets
(Nexus, Artifactory, miroir APT), ou copier les `.deb` téléchargés sur une machine connectée
de même version d'Ubuntu (`apt-get download docker.io docker-compose-v2 containerd runc`
puis `sudo dpkg -i *.deb`).

### 1.4 Vérifier Docker
```bash
docker --version            # attendu : Docker version 24 ou plus
docker compose version      # attendu : Docker Compose version v2.x
sudo docker run --rm hello-world   # seulement si le serveur a accès au registre
```

### 1.5 (Optionnel) Utiliser Docker sans `sudo`
```bash
sudo usermod -aG docker $USER
# se déconnecter puis se reconnecter, et vérifier :
docker ps
```
Dans la suite, les commandes sont écrites sans `sudo` devant `docker`. Si vous n'avez pas
fait cette étape, ajoutez `sudo`.

### 1.6 Vérifier l'accès aux API des modèles depuis le serveur
Avant d'aller plus loin, assurez-vous que le serveur joint bien les API internes :
```bash
curl -s http://<serveur-llm>:8000/v1/models
curl -s http://<serveur-embeddings>:8080/v1/models
```
Chaque commande doit renvoyer du JSON qui liste les modèles. Notez le **nom exact** de
chaque modèle : il servira dans `.env`. Si la commande échoue, réglez d'abord le réseau ou
le pare-feu avec l'équipe infrastructure.

---

## 2. Préparer la copie sur le poste Windows

Ouvrez **PowerShell** dans le dossier du projet (`C:\Users\DELL\Desktop\new`).

### 2.1 Créer l'archive du projet
```powershell
cd C:\Users\DELL\Desktop\new
tar -czf ..\contrats-rag.tar.gz --exclude=.venv --exclude=data --exclude=exports --exclude=backups --exclude=dist --exclude=__pycache__ --exclude=.pytest_cache --exclude=*.egg-info --exclude=.env .
```
Le fichier `C:\Users\DELL\Desktop\contrats-rag.tar.gz` est créé. Il pèse quelques centaines
de Ko : il ne contient ni données ni images Docker.

### 2.2 Vérifier le contenu de l'archive
```powershell
tar -tzf ..\contrats-rag.tar.gz | Select-Object -First 30
```
Vous devez voir notamment `./compose.yml`, `./.env.example`, `./scripts/install.sh`,
`./docker/`, `./src/contract_rag/`, `./owui_extensions/`. Vous **ne** devez **pas** voir
`./.env`, `./data/` ni `./.venv/`.

### 2.3 (Si les API sont en HTTPS avec une autorité interne)
Copiez les certificats racine de l'entreprise dans `certs\` **avant** de créer l'archive :
```powershell
copy C:\chemin\autorite-racine-iap.crt certs\
```
Ils seront intégrés aux images au moment de la construction.

---

## 3. Transférer la copie vers le serveur

Choisissez **un** des moyens suivants.

**a) SCP (OpenSSH, inclus dans Windows 10/11) :**
```powershell
scp ..\contrats-rag.tar.gz utilisateur@IP_DU_SERVEUR:/tmp/
```

**b) WinSCP** : glisser-déposer `contrats-rag.tar.gz` dans `/tmp/` du serveur.

**c) Clé USB / partage réseau** : copier l'archive, puis sur le serveur
`cp /media/<cle>/contrats-rag.tar.gz /tmp/`.

**d) GitLab interne** : si le projet y est versionné, sur le serveur
`git clone <url-du-depot> /opt/contrats-rag`, puis passer directement au §4.2.

Vérifier sur le serveur que le fichier est arrivé :
```bash
ls -lh /tmp/contrats-rag.tar.gz
```

---

## 4. Installer le projet sur le serveur

### 4.1 Décompresser dans `/opt/contrats-rag`
```bash
sudo mkdir -p /opt/contrats-rag
sudo tar -xzf /tmp/contrats-rag.tar.gz -C /opt/contrats-rag
sudo chown -R $USER:$USER /opt/contrats-rag
cd /opt/contrats-rag
```

### 4.2 Rendre les scripts exécutables et corriger les fins de ligne
Les fichiers venant de Windows peuvent perdre leur droit d'exécution :
```bash
chmod +x scripts/*.sh docker/postgres/init/*.sh
```
Le dépôt impose déjà des fins de ligne Unix (`.gitattributes`). Si un script échoue malgré
tout avec `/usr/bin/env: 'bash\r'`, convertissez-les :
```bash
sudo apt-get install -y dos2unix
dos2unix scripts/*.sh docker/postgres/init/*.sh
```

### 4.3 (Conseillé) Placer les données sur un volume dédié
Par défaut, les données vont dans `/opt/contrats-rag/data`. Sur un serveur de production,
préférez un disque dédié :
```bash
sudo mkdir -p /srv/contrats-rag/data /srv/contrats-rag/inbox /srv/sauvegardes
sudo chown -R $USER:$USER /srv/contrats-rag /srv/sauvegardes
```
Vous indiquerez ces chemins dans `.env` au §5.4.

### 4.4 Premier passage du script : création de `.env`
```bash
./scripts/install.sh
```
Résultat attendu :
```
Fichier .env créé avec des secrets générés.
Renseignez les endpoints des modèles dans .env (voir docs/CONNEXION_MODELES.md),
puis relancez : ./scripts/install.sh
```
Le script a :
- copié `.env.example` vers `.env` ;
- généré **5 secrets aléatoires** (mot de passe superutilisateur PostgreSQL, mots de passe
  des rôles `owui`, `contrats_ingest`, `contrats_app`, clé secrète Open WebUI) ;
- protégé le fichier (`chmod 600`).

Il s'arrête volontairement pour que vous configuriez les modèles.

---

## 5. Configurer `.env`

```bash
nano .env
```
(`Ctrl+O` puis `Entrée` pour enregistrer, `Ctrl+X` pour quitter.)

### 5.1 Modèles d'IA – **obligatoire**
```env
# LLM (format OpenAI /v1/chat/completions)
LLM_BASE_URL=http://llm.interne.local:8000/v1
LLM_API_KEY=
LLM_MODEL=<nom exact renvoyé par /v1/models>

# Embeddings (format OpenAI /v1/embeddings)
EMBEDDING_BASE_URL=http://embeddings.interne.local:8080/v1
EMBEDDING_API_KEY=
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIM=1024                 # 1024 pour bge-m3, 768 pour nomic-embed-text…
EMBEDDING_QUERY_PREFIX=            # ex. "query: " pour e5, "search_query: " pour nomic
EMBEDDING_DOCUMENT_PREFIX=         # ex. "passage: " pour e5, "search_document: " pour nomic

# Reranker (facultatif)
RERANKER_API=cohere                # cohere | tei | none
RERANKER_BASE_URL=http://reranker.interne.local:8080/v1
RERANKER_MODEL=BAAI/bge-reranker-v2-m3

# OCR des PDF scannés
OCR_ENGINE=tesseract               # tesseract (inclus, local) | vision | none
OCR_LANGUAGES=fra+eng
```

> ⚠️ **`EMBEDDING_DIM` doit être juste dès la première ingestion.** S'il change plus tard, il
> faudra tout revectoriser (`reindex --all`).
> Les préfixes contenant un espace doivent être entre guillemets : `"query: "`.

### 5.2 Sécurité réseau – **obligatoire**
```env
AI_ALLOWED_HOSTS=.interne.local,10.20.30.40   # seuls hôtes IA autorisés (domaine ou IP)
AI_VERIFY_TLS=true                            # vérifie les certificats HTTPS (CA dans certs/)
```
Tout hôte absent de cette liste est refusé par l'application, même s'il apparaît dans une
URL. Mettez-y **tous** les hôtes utilisés dans les `*_BASE_URL`.

### 5.3 Interface web
```env
OWUI_BIND=0.0.0.0          # 127.0.0.1 si un proxy HTTPS local est installé (§9.3)
OWUI_PORT=3000
WEBUI_URL=http://IP_DU_SERVEUR:3000     # adresse que tapent les utilisateurs
CORS_ALLOW_ORIGIN=http://IP_DU_SERVEUR:3000
TZ=Africa/Algiers
```

### 5.4 Stockage
```env
DATA_ROOT=/srv/contrats-rag/data      # ou ./data par défaut
INBOX_DIR=/srv/contrats-rag/inbox     # ou ./inbox par défaut
```

### 5.5 Comptes et droits
```env
ENABLE_SIGNUP=true            # pour créer le premier compte ; passer à false ensuite (§7.1)
DEFAULT_USER_ROLE=pending     # les nouveaux comptes attendent la validation d'un admin
DEFAULT_CONTRACT_ACCESS=all   # all pour un POC ; none en production (droits explicites, §7.5)
```

### 5.6 À ne pas modifier sans raison
| Variable | Pourquoi |
|---|---|
| `POSTGRES_*_PASSWORD`, `*_PASSWORD`, `WEBUI_SECRET_KEY` | générés automatiquement ; les changer après le 1er démarrage casse l'accès aux bases |
| `OWUI_VERSION` | version d'Open WebUI testée avec les extensions |
| `POSTGRES_IMAGE` | version de PostgreSQL et pgvector ; doit rester identique pour une restauration |

Vérifier qu'aucun secret n'est resté à sa valeur par défaut :
```bash
grep -n 'change-me' .env || echo "OK : aucun secret par défaut"
```

---

## 6. Lancer l'installation et vérifier

### 6.1 Second passage du script
```bash
./scripts/install.sh
```
Le script enchaîne, avec jusqu'à 5 nouvelles tentatives en cas d'erreur réseau :
1. construction des images `contrats-rag/open-webui` et `contrats-rag/ingest`
   (5 à 15 minutes la première fois) ;
2. démarrage de PostgreSQL, qui crée les bases `openwebui` et `contrats` et les 3 rôles ;
3. application du schéma métier (migrations) ;
4. démarrage d'Open WebUI ;
5. contrôle de chaque endpoint de modèle (`check`).

Résultat attendu à la fin :
```
✅ Base contrats         PostgreSQL 17.x, pgvector 0.8.0
✅ Stockage documents    /data/contrats
✅ LLM                   <modèle> @ <url> → « OK »
✅ Embeddings            <modèle> @ <url>, dimension 1024
✅ Reranker              … (ou « désactivé »)
✅ OCR                   Tesseract 5.x (fra+eng)
```

### 6.2 Vérifications complémentaires
```bash
docker compose ps
```
Attendu : `postgres` et `open-webui` à l'état `running (healthy)`. Le service `ingest`
n'apparaît pas : il ne tourne qu'à la demande.

```bash
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:3000/
```
Attendu : `HTTP 200`.

Relancer le contrôle des modèles à tout moment :
```bash
make check        # équivaut à : docker compose run --rm ingest check
```

### 6.3 Si un contrôle est en ❌
Corrigez `.env` (le message indique la cause), puis :
```bash
make restart      # recrée Open WebUI avec la nouvelle configuration
make check
```
Pas besoin de relancer `install.sh` pour un simple changement de `.env`.

---

## 7. Mettre en service dans Open WebUI

### 7.1 Créer le compte administrateur
1. Depuis un poste du réseau, ouvrir `http://IP_DU_SERVEUR:3000`.
2. Créer le premier compte : **il devient administrateur**.
3. Fermer les inscriptions libres :
   ```bash
   nano .env            # ENABLE_SIGNUP=false
   make restart
   ```
   (Ou brancher LDAP / Active Directory / OIDC dans Panneau d'administration > Paramètres.)

### 7.2 Publier les extensions (Tool + Function filtre)
**Méthode automatique (recommandée) :**
1. Dans Open WebUI : Profil > Paramètres > Compte > **Clés API** > créer une clé `sk-…`.
   (Si la section n'apparaît pas, l'activer dans Panneau d'administration > Paramètres >
   Général > « Activer les clés API ».)
2. Sur le serveur :
   ```bash
   make deploy-tools KEY=sk-xxxxxxxxxxxx
   ```
   Le Tool `contrats_recherche` et la Function filtre `contrats_contexte` sont publiés et la
   Function est activée.

**Méthode manuelle** : copier le contenu de
`owui_extensions/tools/contrats_recherche.py` dans Espace de travail > Outils > « + », et
celui de `owui_extensions/functions/contrats_contexte.py` dans Panneau d'administration >
Fonctions > « + », puis activer la fonction.

### 7.3 Créer le modèle « Assistant Contrats »
Espace de travail > Modèles > « + » :
| Champ | Valeur |
|---|---|
| Nom | Assistant Contrats |
| Modèle de base | le LLM interne |
| Prompt système | contenu de `owui_extensions/prompts/assistant_contrats_system.md` |
| **Filtres** | ✅ **Contrats – Recherche systématique (RAG forcé)**, indispensable |
| Outils | ✅ Contrats – Recherche et consultation |
| Paramètres avancés | appel de fonctions : **natif** |
| Visibilité | groupes concernés uniquement |

> Le filtre « RAG forcé » garantit que chaque question est appuyée sur les documents, même si
> le LLM n'appelle pas les outils de lui-même. En production, associez-le **à ce modèle** et
> non globalement.

### 7.4 Créer les groupes
Panneau d'administration > Utilisateurs > Groupes : par exemple `Etudes`, `Projets`,
`Direction`. Y ajouter les utilisateurs.

### 7.5 Définir les droits sur les contrats (production)
Dans `.env` : `DEFAULT_CONTRACT_ACCESS=none`, puis `make restart`. Ensuite :
```bash
docker compose run --rm ingest access grant "group:Etudes"                        # tous les contrats
docker compose run --rm ingest access grant "user:nom@iap.dz" --contrat REF-001   # un seul contrat
docker compose run --rm ingest access list
```

---

## 8. Charger les contrats

### 8.1 (Optionnel) Importer le référentiel des contrats
À partir d'un CSV (modèle : `docs/exemples/contrats.csv`, détails dans
[docs/IMPORT_CONTRATS.md](docs/IMPORT_CONTRATS.md)) :
```bash
cp mon-referentiel.csv inbox/      # ou le dossier défini par INBOX_DIR, monté en /inbox
docker compose run --rm ingest contracts import /inbox/mon-referentiel.csv
docker compose run --rm ingest contracts list
```

### 8.2 Déposer les documents
Un sous-dossier par contrat, nommé avec sa référence :
```
inbox/
├── IAP-EP-2023-014/
│   ├── contrat.pdf
│   ├── avenant_01.docx
│   └── BPU.xlsx
└── IAP-EP-2024-003/
    └── ...
```
Depuis Windows : `scp -r .\IAP-EP-2023-014 utilisateur@IP_DU_SERVEUR:/srv/contrats-rag/inbox/`
(ou le chemin de `INBOX_DIR`).

### 8.3 Ingérer et vérifier
```bash
make ingest                                   # fichiers déjà indexés : ignorés automatiquement
make documents                                # statut : indexe / a_vectoriser / erreur
make search Q="pénalités de retard"
make show REF=IAP-EP-2023-014
```
Les PDF scannés passent automatiquement par l'OCR. Un document en `a_vectoriser` (API
d'embeddings indisponible pendant l'ingestion) se rattrape avec `make reindex`.

### 8.4 Tester dans l'interface
Choisir le modèle **Assistant Contrats** et poser une question, par exemple « Quel est le
taux des pénalités de retard du contrat IAP-EP-2023-014 ? ». La réponse doit citer ses
sources (fichier, page, article ou cellule Excel).

---

## 9. Sécuriser le serveur

### 9.1 Pare-feu
```bash
sudo ufw allow OpenSSH
sudo ufw allow 3000/tcp          # ou 443/tcp si proxy HTTPS (§9.3)
sudo ufw enable
sudo ufw status
```
> ⚠️ Docker publie ses ports **en contournant ufw**. Pour limiter réellement l'accès au port
> 3000, mettez `OWUI_BIND=127.0.0.1` derrière un proxy (§9.3) ou filtrez dans la chaîne
> `DOCKER-USER` (exemple dans [docs/POINTS_A_VERIFIER.md](docs/POINTS_A_VERIFIER.md)).
> PostgreSQL n'est jamais exposé sur le réseau.

### 9.2 Bloquer les sorties Internet
Open WebUI tourne en mode hors ligne (`OFFLINE_MODE`) et l'application n'appelle que les
hôtes de `AI_ALLOWED_HOSTS`. Pour une garantie au niveau réseau, interdisez les sorties des
conteneurs vers Internet au pare-feu (chaîne `DOCKER-USER`, ou pare-feu de l'entreprise),
en n'autorisant que les IP des API de modèles.

### 9.3 (Conseillé) HTTPS avec un proxy local
Exemple avec Nginx et un certificat interne :
```bash
sudo apt-get install -y nginx
sudo nano /etc/nginx/sites-available/contrats-rag
```
```nginx
server {
    listen 443 ssl;
    server_name contrats.iap.local;
    ssl_certificate     /etc/ssl/iap/contrats.crt;
    ssl_certificate_key /etc/ssl/iap/contrats.key;
    client_max_body_size 200M;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;       # WebSocket (réponses en flux)
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 600s;
    }
}
```
```bash
sudo ln -s /etc/nginx/sites-available/contrats-rag /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```
Puis dans `.env` : `OWUI_BIND=127.0.0.1`, `WEBUI_URL=https://contrats.iap.local`,
`CORS_ALLOW_ORIGIN=https://contrats.iap.local`, et `make restart`.

### 9.4 Protéger les secrets
```bash
ls -l .env            # doit afficher -rw------- (600)
```
Ne mettez jamais `.env` dans un dépôt Git ni dans un e-mail. Il est inclus dans les
sauvegardes (`env.backup`) : protégez aussi le dossier des sauvegardes.

---

## 10. Sauvegardes automatiques et redémarrage

### 10.1 Sauvegarde manuelle
```bash
./scripts/backup.sh /srv/sauvegardes
```
Crée `/srv/sauvegardes/AAAAMMJJ-HHMMSS/` avec les 2 bases, les rôles, les fichiers, la
configuration et un fichier d'empreintes `SHA256SUMS`.

### 10.2 Sauvegarde quotidienne automatique (2 h du matin)
```bash
sudo crontab -e
```
Ajouter la ligne :
```cron
0 2 * * * cd /opt/contrats-rag && ./scripts/backup.sh /srv/sauvegardes >> /var/log/contrats-rag-backup.log 2>&1
```
Pensez à purger les anciennes sauvegardes (par exemple, garder 14 jours) :
```cron
30 3 * * * find /srv/sauvegardes -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} +
```
Et à copier régulièrement `/srv/sauvegardes` vers un autre serveur ou un stockage externe.

### 10.3 Redémarrage du serveur
Les services ont `restart: unless-stopped` : ils repartent seuls au démarrage, tant que
Docker est activé (`sudo systemctl enable docker`). Pour le vérifier :
```bash
sudo reboot
# après reconnexion :
cd /opt/contrats-rag && docker compose ps
```

### 10.4 Commandes d'exploitation utiles
| Action | Commande |
|---|---|
| État | `docker compose ps` |
| Journaux | `make logs` |
| Arrêter / démarrer | `make down` / `make up` |
| Après modification de `.env` | `make restart && make check` |
| Liste de tous les raccourcis | `make help` |

---

## 11. Cas particulier : migrer une installation avec ses données

Pour déplacer une installation existante (ancien serveur, ou WSL de test) **avec** ses
contrats indexés, comptes, extensions et conversations.

### 11.1 Sur l'ancienne machine
```bash
cd /opt/contrats-rag
./scripts/backup.sh /srv/sauvegardes
ls /srv/sauvegardes/            # noter le dossier AAAAMMJJ-HHMMSS créé
```
Copier le dossier de sauvegarde vers le nouveau serveur :
```bash
scp -r /srv/sauvegardes/AAAAMMJJ-HHMMSS utilisateur@NOUVEAU_SERVEUR:/srv/sauvegardes/
```
Depuis WSL, le dossier est aussi visible sous Windows (`\\wsl$\Ubuntu-24.04\srv\sauvegardes`),
ce qui permet de le copier avec WinSCP ou une clé USB.

### 11.2 Sur le nouveau serveur
1. Faire les §1 à §4.3 (Docker, copie du projet, décompression). **Ne pas** lancer
   `install.sh` tout de suite.
2. Reprendre la configuration de l'ancienne installation (mêmes mots de passe, sinon les
   bases restaurées seraient inaccessibles) :
   ```bash
   cd /opt/contrats-rag
   cp /srv/sauvegardes/AAAAMMJJ-HHMMSS/env.backup .env
   chmod 600 .env
   nano .env       # adapter si besoin : DATA_ROOT, INBOX_DIR, OWUI_PORT, WEBUI_URL, URL des modèles
   ```
3. Installer :
   ```bash
   ./scripts/install.sh
   ```
4. Restaurer les données :
   ```bash
   ./scripts/restore.sh /srv/sauvegardes/AAAAMMJJ-HHMMSS
   ```
   Le script demande de taper `OUI` : il remplace les bases et les fichiers.
5. Vérifier :
   ```bash
   make check
   make documents
   make search Q="pénalités de retard"
   ```
   Puis se connecter à l'interface avec un compte de l'ancienne installation : les modèles,
   le Tool, la Function et les conversations sont présents.

> Ne gardez pas `POSTGRES_IMAGE` différente entre les deux serveurs. La restauration passe
> par `pg_dump`/`pg_restore` et reste plus sûre que la copie brute de `data/`.

---

## 12. Cas particulier : serveur sans Internet

Le serveur cible ne peut ni télécharger ni construire les images : on lui apporte un
**paquet complet** qui contient les 3 images Docker.

### 12.1 Sur une machine connectée (Linux ou WSL avec Docker)
Faire les §2 à §4.2 sur cette machine (ou utiliser une installation existante), puis :
```bash
cd /opt/contrats-rag
./scripts/bundle-offline.sh          # ou : make bundle
ls -lh dist/
```
Résultat : `dist/contrats-rag-0.1.0.tar.gz` (≈ 1,8 Go) et `dist/contrats-rag-0.1.0.tar.gz.sha256`.
Le paquet contient les images Open WebUI (dérivée), `ingest` et PostgreSQL + pgvector,
ainsi que tous les fichiers du projet. Les certificats de `certs/` sont intégrés s'ils y
étaient avant la commande.

### 12.2 Transfert
Copier **les deux fichiers** (`.tar.gz` et `.sha256`) sur le serveur isolé : clé USB, disque,
partage réseau interne, ou `scp` via une machine de rebond.

### 12.3 Sur le serveur isolé
Docker doit déjà être installé (§1.3 option 3).
```bash
cd /chemin/du/paquet
sha256sum -c contrats-rag-0.1.0.tar.gz.sha256      # attendu : « Réussi » / « OK »
sudo tar -xzf contrats-rag-0.1.0.tar.gz -C /opt
sudo chown -R $USER:$USER /opt/contrats-rag-0.1.0
cd /opt/contrats-rag-0.1.0
./scripts/install.sh        # 1er passage : crée .env
nano .env                   # §5
./scripts/install.sh        # charge les images (docker load), démarre, migre, contrôle
```
Le script détecte le dossier `images/` et **charge** les images au lieu de les construire :
aucun accès Internet n'est nécessaire. Continuer ensuite au §7.

---

## 13. Liste de contrôle finale

- [ ] `docker compose ps` : `postgres` et `open-webui` en `healthy`
- [ ] `make check` : tous les contrôles en ✅
- [ ] `grep change-me .env` ne renvoie rien ; `.env` en droits `600`
- [ ] Interface accessible depuis un poste utilisateur
- [ ] Compte administrateur créé, puis `ENABLE_SIGNUP=false`
- [ ] Tool et Function publiés ; modèle « Assistant Contrats » créé avec le filtre « RAG forcé »
- [ ] Documents ingérés : `make documents` sans `erreur` ni `a_vectoriser`
- [ ] Une question test renvoie une réponse **avec sources**
- [ ] Droits définis et `DEFAULT_CONTRACT_ACCESS=none` (production)
- [ ] Pare-feu en place ; sorties Internet bloquées ; HTTPS si possible
- [ ] Sauvegarde quotidienne planifiée **et** une restauration testée au moins une fois
- [ ] Redémarrage du serveur testé : les services repartent seuls

---

## 14. Dépannage rapide

| Symptôme | Solution |
|---|---|
| `permission denied` sur un script | `chmod +x scripts/*.sh docker/postgres/init/*.sh` |
| `/usr/bin/env: 'bash\r'` | `dos2unix scripts/*.sh docker/postgres/init/*.sh` |
| `permission denied … docker.sock` | ajouter `sudo`, ou §1.5 puis se reconnecter |
| `Des secrets valent encore 'change-me'` | supprimer `.env` et relancer `install.sh` (nouvelle installation uniquement) |
| Build bloqué : `lookup … i/o timeout`, `network is unreachable` | DNS ou proxy du serveur : relancer `install.sh` (il réessaie 5 fois). Derrière un proxy, configurer le proxy de Docker, ou utiliser le §12 |
| `❌ … n'est pas dans CRAG_ALLOWED_HOSTS` | ajouter l'hôte dans `AI_ALLOWED_HOSTS`, `make restart` |
| `❌ Embeddings … Dimension X ≠ …` | corriger `EMBEDDING_DIM`, puis `docker compose run --rm ingest reindex --all` |
| `ConnectTimeout` vers un modèle | tester depuis le conteneur : `docker compose run --rm --entrypoint curl ingest -s <URL>/models` |
| `CERTIFICATE_VERIFY_FAILED` | copier la CA dans `certs/`, `docker compose --profile cli build`, `docker compose up -d` |
| Interface inaccessible | `docker compose ps`, `ss -ltnp \| grep 3000`, pare-feu, valeur de `OWUI_BIND` |
| PostgreSQL ne démarre pas | `docker compose logs postgres` ; vérifier les droits sur `DATA_ROOT` |
| L'assistant invente des réponses | vérifier que le filtre « RAG forcé » est coché sur le modèle (§7.3) |
| Document en `erreur` | `make documents` pour le message, puis `docker compose run --rm ingest ingest <fichier> --force` |

Pour aller plus loin : [GUIDE_DEPLOIEMENT.md](GUIDE_DEPLOIEMENT.md) §10 (dépannage complet)
et §9 (mises à jour).
