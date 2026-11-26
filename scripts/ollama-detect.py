#!/usr/bin/env python3
"""Détecte les modèles d'un serveur Ollama et en déduit la configuration de Contrats RAG.

Usage : scripts/ollama-detect.py http://172.21.36.55:11434
Sortie : lignes CLE=valeur (lues par deploy.sh), sans guillemets superflus.

Règles de choix (modifiables ensuite dans .env) :
  - embeddings : nom contenant « embed » (Qwen3-Embedding de préférence) ;
  - OCR / vision : nom contenant « ocr » (sinon « vl » ou « vision ») ;
  - LLM : les autres modèles, Qwen de préférence, le plus gros d'abord.
La dimension native des embeddings est mesurée par un appel réel ; au-delà de 2000
(limite des index pgvector), on demande 1024 dimensions (modèles Matryoshka).
Bibliothèque standard uniquement (aucune dépendance).
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request

PGVECTOR_MAX_DIM = 2000
REDUCED_DIM = 1024

PREFIXES = {  # (requête, document) recommandés par les auteurs des modèles
    "qwen3-embedding": (
        r"Instruct: Given a question about a public works contract, retrieve the contract passages "
        r"that answer it\nQuery: ",
        "",
    ),
    "nomic-embed": ("search_query: ", "search_document: "),
    "e5": ("query: ", "passage: "),
}


def _get(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def _post(url: str, payload: dict, timeout: float = 300.0):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def choose_models(models: list[dict]) -> dict[str, str]:
    """Répartit les modèles d'Ollama entre LLM, embeddings et vision (fonction pure, testée)."""
    names = [(m.get("name") or m.get("model") or "", int(m.get("size") or 0)) for m in models]
    names = [(n, s) for n, s in names if n]

    def low(n: str) -> str:
        return n.lower()

    embed = [n for n, _ in names if "embed" in low(n)]
    embed.sort(key=lambda n: (0 if "qwen" in low(n) else 1, n))
    vision = [n for n, _ in names if "ocr" in low(n)]
    if not vision:
        vision = [n for n, _ in names if re.search(r"(^|[^a-z])(vl|vision)([^a-z]|$)", low(n))]
    used = set(embed[:1]) | set(vision[:1])
    llm = [(n, s) for n, s in names if n not in used and "embed" not in low(n) and "ocr" not in low(n)]
    llm.sort(key=lambda x: (0 if "qwen" in low(x[0]) else 1, -x[1], x[0]))
    return {
        "LLM_MODEL": llm[0][0] if llm else "",
        "EMBEDDING_MODEL": embed[0] if embed else "",
        "VISION_MODEL": vision[0] if vision else "",
    }


def embedding_settings(model: str, native_dim: int) -> dict[str, str]:
    """Dimension et préfixes à utiliser pour un modèle d'embeddings (fonction pure, testée)."""
    out: dict[str, str] = {}
    if native_dim > PGVECTOR_MAX_DIM:
        out.update(EMBEDDING_DIM=str(REDUCED_DIM), EMBEDDING_REQUEST_DIMENSIONS="true",
                   EMBEDDING_TRUNCATE="true", OWUI_BYPASS_EMBEDDING="true")
    elif native_dim > 0:
        out.update(EMBEDDING_DIM=str(native_dim), EMBEDDING_REQUEST_DIMENSIONS="false",
                   EMBEDDING_TRUNCATE="false")
    for key, (query, document) in PREFIXES.items():
        if key in model.lower():
            out["EMBEDDING_QUERY_PREFIX"] = f'"{query}"'
            out["EMBEDDING_DOCUMENT_PREFIX"] = f'"{document}"' if document else ""
            break
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    base = argv[1].rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    try:
        version = _get(f"{base}/api/version").get("version", "?")
        models = _get(f"{base}/api/tags").get("models", [])
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"ERREUR=Ollama injoignable sur {base} : {exc}")
        return 1
    chosen = choose_models(models)
    result = {"OLLAMA_VERSION": version, "OLLAMA_MODELS": ",".join(m.get("name", "") for m in models)}
    result.update(chosen)
    if chosen["EMBEDDING_MODEL"]:
        try:
            data = _post(f"{base}/v1/embeddings", {"model": chosen["EMBEDDING_MODEL"], "input": ["test"]})
            native = len(data["data"][0]["embedding"])
            result["EMBEDDING_NATIVE_DIM"] = str(native)
            result.update(embedding_settings(chosen["EMBEDDING_MODEL"], native))
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as exc:
            result["AVERTISSEMENT"] = f"dimension des embeddings non mesurée ({exc})"
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
