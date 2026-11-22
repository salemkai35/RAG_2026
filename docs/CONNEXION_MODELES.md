# Connexion aux API internes des modèles

Tous les modèles sont appelés au **format compatible OpenAI**. La connexion se règle
uniquement dans `.env`. Le même fichier configure Open WebUI (chat, RAG natif) et
le package métier (Tools, ingestion).

| Rôle | Variables `.env` | Appel effectué |
|---|---|---|
| LLM | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | `POST {LLM_BASE_URL}/chat/completions` |
| Embeddings | `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIM` | `POST {EMBEDDING_BASE_URL}/embeddings` |
| Reranker | `RERANKER_API`, `RERANKER_BASE_URL`, `RERANKER_API_KEY`, `RERANKER_MODEL` | `POST {RERANKER_BASE_URL}/rerank` |
| OCR vision | `OCR_ENGINE=vision`, `VISION_BASE_URL`, `VISION_API_KEY`, `VISION_MODEL` | `POST {VISION_BASE_URL}/chat/completions` (image en base64) |
| OCR local | `OCR_ENGINE=tesseract`, `OCR_LANGUAGES=fra+eng` | aucun appel réseau |

Règles :
- `*_BASE_URL` **inclut `/v1`** pour les serveurs OpenAI-compatibles (vLLM, Ollama,
  Infinity, passerelles). Pour TEI, l'URL est la racine du serveur, sans `/v1`.
- `*_API_KEY` est envoyée en `Authorization: Bearer …`. Laisser la variable vide si
  l'endpoint ne demande pas de clé.
- `AI_ALLOWED_HOSTS` est une liste blanche : tout appel vers un autre hôte est refusé
  par le package (ex. `.interne.local,10.20.30.40`).
- Certificats internes : déposer les CA dans `certs/*.crt`, puis reconstruire les
  images (`make build`).
- Les variables `HTTP(S)_PROXY` sont **ignorées** par le package, pour que les appels
  restent sur le réseau interne. Pour les prendre en compte, ajouter
  `CRAG_HTTP_TRUST_ENV=true` dans `compose.yml`.

Après chaque modification de `.env` :

```bash
make restart   # recharge Open WebUI
make check     # teste chaque endpoint (✅ / ❌ avec le message d'erreur)
```

## Exemples de serveurs internes

### vLLM (LLM, embeddings et reranker)
```env
LLM_BASE_URL=http://gpu01.interne.local:8000/v1
LLM_MODEL=mistralai/Mistral-Small-3.2-24B-Instruct-2506   # nom exposé par /v1/models
EMBEDDING_BASE_URL=http://gpu01.interne.local:8001/v1
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIM=1024
RERANKER_API=cohere
RERANKER_BASE_URL=http://gpu01.interne.local:8002/v1
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
```
Les noms de modèles ci-dessus sont des exemples. Utilisez ceux que publie votre serveur
(`curl $LLM_BASE_URL/models`).

### Hugging Face Text Embeddings Inference (TEI)
```env
EMBEDDING_BASE_URL=http://tei-embed.interne.local:8080/v1     # route OpenAI de TEI
RERANKER_API=tei
RERANKER_BASE_URL=http://tei-rerank.interne.local:8080        # sans /v1
```

### Ollama (poste de développement)
```env
LLM_BASE_URL=http://ollama.interne.local:11434/v1
EMBEDDING_BASE_URL=http://ollama.interne.local:11434/v1
EMBEDDING_MODEL=bge-m3
RERANKER_API=none
```

### Passerelle unique (LiteLLM ou équivalent interne)
Indiquer la même URL et la même clé pour chaque rôle. Seuls les noms de modèles changent.

## Changer de modèle d'embedding
1. Modifier `EMBEDDING_MODEL` et `EMBEDDING_DIM`.
2. `make restart`, puis `docker compose run --rm ingest reindex --all`. Les vecteurs
   de l'index métier sont recalculés, et la colonne est recréée si la dimension change.
3. RAG natif d'Open WebUI : Admin > Paramètres > Documents > « Réindexer ». Attention :
   Open WebUI fixe la dimension de sa table pgvector à la création
   (`PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH`) ; voir docs/POINTS_A_VERIFIER.md.

## Modèles à privilégier (à confirmer selon le catalogue interne)
- **Embeddings** : un modèle multilingue performant en français (famille bge-m3 ou
  multilingual-e5). Le modèle par défaut d'Open WebUI (`all-MiniLM-L6-v2`) est anglophone
  et **inadapté**.
  - Pour e5 : `EMBEDDING_QUERY_PREFIX="query: "` et `EMBEDDING_DOCUMENT_PREFIX="passage: "`.
- **Reranker** : un modèle multilingue (famille bge-reranker-v2-m3).
- **LLM** : il doit maîtriser le français et l'**appel de fonctions natif** (tool calling),
  avec un contexte d'au moins 32k tokens.
