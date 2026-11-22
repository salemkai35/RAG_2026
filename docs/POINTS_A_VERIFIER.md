# Points à valider sur la version épinglée d'Open WebUI (POC)

Les paramètres ci-dessous ont été vérifiés dans le **code source** d'Open WebUI (branche main,
novembre 2026, dernière version publiée : v0.13.4) :
- `DATABASE_URL`, `VECTOR_DB=pgvector` et `PGVECTOR_DB_URL` (vaut `DATABASE_URL` par défaut) ;
- `PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH` (1536 par défaut, maximum 2000 sans halfvec) ;
- `RAG_OPENAI_API_BASE_URL` (**vaut api.openai.com par défaut : à surcharger**) ;
- `RAG_EXTERNAL_RERANKER_URL`, `ENABLE_RAG_HYBRID_SEARCH`, `OFFLINE_MODE`,
  `STORAGE_PROVIDER`, `REDIS_URL`, `UVICORN_WORKERS`, `ENABLE_SCIM`, `TOOL_SERVER_CONNECTIONS` ;
- la licence « Open WebUI License », avec sa clause de marque au-delà de 50 utilisateurs
  sur 30 jours.

Les points suivants **n'ont pas pu être confirmés** et doivent être testés pendant le POC :

| # | Point | Comment le vérifier | Impact si différent |
|---|---|---|---|
| 1 | Valeurs exactes de `RAG_RERANKING_ENGINE` pour un reranker externe | Admin > Documents > Reranking, puis observer la requête | Reranking du **RAG natif** uniquement. L'index métier utilise son propre client. |
| 2 | Existence de `ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS` | Enregistrer un Tool contenant `requirements:` sans Internet | Aucun si le Tool n'a pas de `requirements:` (cas actuel). |
| 3 | ✅ **Validé (v0.13.4, 21/11/2026)** – événement `citation` | 6 sources (fichier, page, tableau) affichées dans la conversation | — |
| 4 | ✅ **Validé** – groupes : API `Groups.get_groups_by_member_id` **asynchrone** en v0.13 | Avertissement `never awaited` disparu après correction | `owui/adapters.resolve_user` gère les versions synchrones et asynchrones. |
| 5 | ✅ **Validé** – endpoints `/api/v1/tools/…` et `/api/v1/functions/…` (création, mise à jour, `toggle`, `toggle/global`) | `owui deploy` : Tool et Function publiés et activés | — |
| 6 | Dimension de la table pgvector du RAG natif après changement de modèle | Changer de modèle, puis réindexer | Il peut être nécessaire de vider la table `document_chunk` de la base `openwebui`. |
| 7 | Fonctionnement hors Internet | Bloquer la sortie réseau du serveur (pare-feu), redémarrer, tester | Aucun appel externe ne doit apparaître dans les journaux. |
| 8 | Qualité de l'appel de fonctions du LLM interne | 20 questions types (`eval/`) | **Constaté en test** : Mistral 7B n'appelle pas les outils et invente. Le filtre « RAG forcé » (`contrats_contexte`) rend la recherche indépendante du LLM ; vérifier quand même la capacité d'appel d'outils du LLM interne pour la fiche contrat et les futurs Tools. |

## Blocage réseau sortant (recommandé en production)
Les variables d'environnement ne suffisent pas comme garantie. Au niveau du serveur, n'autoriser
en sortie des conteneurs que les sous-réseaux des API internes. Exemple avec la chaîne
`DOCKER-USER` (à adapter par l'équipe réseau) :

```bash
# Autoriser le sous-réseau des API IA internes, bloquer toute autre sortie des conteneurs
iptables -I DOCKER-USER 1 -s 172.16.0.0/12 -d 10.20.0.0/16 -j ACCEPT
iptables -I DOCKER-USER 2 -s 172.16.0.0/12 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -I DOCKER-USER 3 -s 172.16.0.0/12 ! -d 172.16.0.0/12 -j DROP
```
