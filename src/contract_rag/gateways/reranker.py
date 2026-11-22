"""Client de reranking.

Deux formats d'API sont pris en charge :

* ``cohere`` – POST {base}/rerank  {"model","query","documents","top_n"}
  -> {"results": [{"index", "relevance_score"}]}
  (vLLM, Infinity, Jina, Cohere, LiteLLM…)
* ``tei``    – POST {base}/rerank  {"query","texts"}
  -> [{"index", "score"}]   (Text Embeddings Inference)
"""

from __future__ import annotations

from typing import Any

from ..config import Settings, get_settings
from .http import ModelEndpointError, build_client, post_json


def build_rerank_request(api: str, model: str | None, query: str, documents: list[str], top_n: int) -> dict:
    if api == "tei":
        return {"query": query, "texts": documents, "raw_scores": False, "truncate": True}
    if api == "cohere":
        payload: dict[str, Any] = {"query": query, "documents": documents, "top_n": top_n}
        if model:
            payload["model"] = model
        return payload
    raise ValueError(f"API de reranking inconnue : {api}")


def parse_rerank_response(data: Any) -> list[tuple[int, float]]:
    """Normalise les deux formats en [(index, score)] trié par score décroissant."""
    if isinstance(data, dict) and "results" in data:
        items = data["results"]
        pairs = [(int(i["index"]), float(i.get("relevance_score", i.get("score", 0.0)))) for i in items]
    elif isinstance(data, list):
        pairs = [(int(i["index"]), float(i.get("score", i.get("relevance_score", 0.0)))) for i in data]
    else:
        raise ModelEndpointError(f"Réponse de reranking inattendue : {str(data)[:300]}")
    return sorted(pairs, key=lambda p: p[1], reverse=True)


class RerankerClient:
    def __init__(self, settings: Settings | None = None, retries: int = 3):
        self.settings = settings or get_settings()
        self.endpoint = self.settings.reranker
        self.retries = retries
        self.api = self.settings.reranker_api
        self._client = None

    @property
    def enabled(self) -> bool:
        return self.api != "none" and self.endpoint.configured

    @property
    def client(self):
        if self._client is None:
            self._client = build_client(self.endpoint, self.settings)
        return self._client

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float | None]]:
        if not documents:
            return []
        if not self.enabled:
            return [(i, None) for i in range(min(top_n, len(documents)))]
        payload = build_rerank_request(self.api, self.endpoint.model, query, documents, top_n)
        data = post_json(self.client, self.endpoint, "/rerank", payload, self.retries)
        return parse_rerank_response(data)[:top_n]

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
