# Raccourcis d'exploitation (serveur Linux). Toutes les commandes passent par Docker Compose.
COMPOSE = docker compose
CLI     = $(COMPOSE) run --rm ingest

.PHONY: help install up down restart logs build bundle migrate check ingest reindex search show \
        documents access deploy-tools backup restore test deploy commit git-setup

help:          ## Liste des commandes
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

deploy:        ## Déploiement complet sur serveur vierge (prérequis + installation)
	sudo ./deploy.sh

install:       ## Installation / mise à jour complète
	./scripts/install.sh

up:            ## Démarrer PostgreSQL + Open WebUI
	$(COMPOSE) up -d

down:          ## Arrêter la pile
	$(COMPOSE) down

restart:       ## Redémarrer Open WebUI (après modification de .env)
	$(COMPOSE) up -d --force-recreate open-webui

logs:          ## Journaux d'Open WebUI
	$(COMPOSE) logs -f --tail=200 open-webui

build:         ## Construire les images
	$(COMPOSE) --profile cli build

bundle:        ## Paquet d'installation hors ligne (dist/)
	./scripts/bundle-offline.sh

migrate:       ## Appliquer les migrations du schéma métier
	$(CLI) db upgrade

check:         ## Contrôler base, stockage et endpoints modèles
	$(CLI) check

ingest:        ## Ingérer le dossier inbox/ (un sous-dossier par contrat)
	$(CLI) ingest /inbox

reindex:       ## Vectoriser les chunks en attente
	$(CLI) reindex

search:        ## Tester la recherche : make search Q="pénalités de retard"
	$(CLI) search "$(Q)"

show:          ## Fiche contrat : make show REF=IAP-EP-2023-014
	$(CLI) show "$(REF)"

documents:     ## Lister les documents indexés
	$(CLI) documents

access:        ## Lister les droits d'accès métier
	$(CLI) access list

deploy-tools:  ## Publier les Tools dans Open WebUI : make deploy-tools KEY=sk-...
	$(CLI) owui deploy --api-key "$(KEY)"

backup:        ## Sauvegarde complète (backups/)
	./scripts/backup.sh

restore:       ## Restauration : make restore DIR=backups/AAAAMMJJ-HHMMSS
	./scripts/restore.sh "$(DIR)"

test:          ## Tests unitaires (Python local)
	python -m pytest -q

git-setup:     ## Active les garde-fous Git (anti-secrets) : make git-setup [REMOTE=git@github.com:…]
	./scripts/git-setup.sh $(REMOTE)

commit:        ## Commit local descriptif (jamais de push) : make commit [M="message"]
	./scripts/auto-commit.sh $(if $(M),"$(M)",)
