"""Client d'embeddings (POST {base}/embeddings)."""

from __future__ import annotations

from ..config import Settings, get_settings
from .http import ModelEndpointError, build_client, post_json


def to_pgvector(vector: list[float]) -> str:
    """Représentation texte acceptée par pgvector : '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


def truncate_normalize(vector: list[float], dim: int) -> list[float]:
    """Réduction Matryoshka côté client : garde les ``dim`` premières composantes puis
    renormalise (norme L2 = 1). Valable uniquement pour les modèles entraînés en MRL
    (Qwen3-Embedding, nomic-embed v1.5…). Un vecteur déjà à la bonne taille est inchangé."""
    if len(vector) <= dim:
        return vector
    head = vector[:dim]
    norm = sum(x * x for x in head) ** 0.5
    return [x / norm for x in head] if norm else head


class EmbeddingClient:
    def __init__(self, settings: Settings | None = None, retries: int = 3):
        self.settings = settings or get_settings()
        self.endpoint = self.settings.embedding
        self.retries = retries
        self._client = None

    @property
    def model_name(self) -> str:
        return self.endpoint.model or "inconnu"

    @property
    def client(self):
        if self._client is None:
            self._client = build_client(self.endpoint, self.settings)
        return self._client

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        prefix = self.settings.embedding_document_prefix
        return self._embed([prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self.settings.embedding_query_prefix + text])[0]

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        if not self.endpoint.model:
            raise ModelEndpointError(f"{self.endpoint.env_prefix}MODEL non défini.")
        max_chars = self.settings.embedding_max_chars
        inputs = [(t or " ")[:max_chars] for t in inputs]
        vectors: list[list[float]] = []
        size = max(1, self.settings.embedding_batch_size)
        for start in range(0, len(inputs), size):
            batch = inputs[start : start + size]
            payload = {"model": self.endpoint.model, "input": batch}
            if self.settings.embedding_request_dimensions:
                # Modèles Matryoshka (Qwen3-Embedding, nomic v1.5, OpenAI v3) : le serveur
                # renvoie directement un vecteur de la dimension demandée (Ollama, vLLM, OpenAI).
                payload["dimensions"] = self.settings.embedding_dim
            data = post_json(self.client, self.endpoint, "/embeddings", payload, self.retries)
            try:
                items = sorted(data["data"], key=lambda item: item.get("index", 0))
                batch_vectors = [item["embedding"] for item in items]
            except (KeyError, TypeError) as exc:
                raise ModelEndpointError(f"Réponse embeddings inattendue : {str(data)[:300]}") from exc
            if len(batch_vectors) != len(batch):
                raise ModelEndpointError(
                    f"Embeddings : {len(batch_vectors)} vecteurs reçus pour {len(batch)} textes."
                )
            if self.settings.embedding_truncate:
                batch_vectors = [truncate_normalize(v, self.settings.embedding_dim) for v in batch_vectors]
            for vec in batch_vectors:
                if len(vec) != self.settings.embedding_dim:
                    raise ModelEndpointError(
                        f"Dimension d'embedding {len(vec)} ≠ CRAG_EMBEDDING_DIM={self.settings.embedding_dim}. "
                        "Corriger EMBEDDING_DIM dans .env puis lancer 'contract-rag reindex --all'."
                    )
            vectors.extend(batch_vectors)
        return vectors

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
