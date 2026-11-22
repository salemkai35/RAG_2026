"""Configuration centralisée, lue exclusivement depuis les variables d'environnement.

Convention : toutes les variables du package sont préfixées ``CRAG_``.
Chaque rôle de modèle (LLM, EMBEDDING, RERANKER, VISION) possède
``CRAG_<ROLE>_BASE_URL``, ``CRAG_<ROLE>_API_KEY``, ``CRAG_<ROLE>_MODEL`` et
``CRAG_<ROLE>_TIMEOUT``. Si une passerelle unique sert tous les modèles,
``CRAG_AI_BASE_URL`` / ``CRAG_AI_API_KEY`` servent de valeurs par défaut.

Changer de modèle ou d'endpoint = changer le fichier .env et redémarrer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "oui", "on"}


def _int(name: str, default: int) -> int:
    value = _env(name)
    return int(value) if value is not None else default


def _float(name: str, default: float) -> float:
    value = _env(name)
    return float(value) if value is not None else default


@dataclass(frozen=True)
class Endpoint:
    """Un endpoint de modèle interne compatible OpenAI."""

    name: str
    base_url: str | None
    api_key: str | None
    model: str | None
    timeout: float = 120.0

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    @property
    def env_prefix(self) -> str:
        return f"CRAG_{self.name.upper()}_"


def _endpoint(role: str, default_timeout: float) -> Endpoint:
    prefix = f"CRAG_{role}_"
    base_url = _env(prefix + "BASE_URL") or _env("CRAG_AI_BASE_URL")
    return Endpoint(
        name=role.lower(),
        base_url=base_url.rstrip("/") if base_url else None,
        api_key=_env(prefix + "API_KEY") or _env("CRAG_AI_API_KEY"),
        model=_env(prefix + "MODEL"),
        timeout=_float(prefix + "TIMEOUT", default_timeout),
    )


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    db_pool_max_size: int
    storage_root: Path

    llm: Endpoint
    embedding: Endpoint
    reranker: Endpoint
    vision: Endpoint

    embedding_dim: int
    embedding_batch_size: int
    embedding_max_chars: int
    embedding_query_prefix: str
    embedding_document_prefix: str
    reranker_api: str  # cohere | tei | none

    ocr_engine: str  # tesseract | vision | none
    ocr_languages: str
    ocr_min_chars: int
    ocr_dpi: int

    chunk_max_chars: int
    chunk_overlap_chars: int
    retrieval_top_k: int
    retrieval_candidates: int

    default_access: str  # none | all
    allowed_hosts: tuple[str, ...]
    verify_tls: bool
    ca_bundle: str | None
    http_trust_env: bool
    owui_extensions_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        ca_bundle = _env("CRAG_CA_BUNDLE")
        if ca_bundle is None and Path(_SYSTEM_CA_BUNDLE).exists():
            ca_bundle = _SYSTEM_CA_BUNDLE
        allowed = tuple(
            h.strip().lower() for h in (_env("CRAG_ALLOWED_HOSTS", "") or "").split(",") if h.strip()
        )
        settings = cls(
            database_url=_env("CRAG_DATABASE_URL"),
            db_pool_max_size=_int("CRAG_DB_POOL_MAX_SIZE", 5),
            storage_root=Path(_env("CRAG_STORAGE_ROOT", "./data/contrats") or "./data/contrats"),
            llm=_endpoint("LLM", 180.0),
            embedding=_endpoint("EMBEDDING", 120.0),
            reranker=_endpoint("RERANKER", 60.0),
            vision=_endpoint("VISION", 180.0),
            embedding_dim=_int("CRAG_EMBEDDING_DIM", 1024),
            embedding_batch_size=_int("CRAG_EMBEDDING_BATCH_SIZE", 32),
            embedding_max_chars=_int("CRAG_EMBEDDING_MAX_CHARS", 6000),
            embedding_query_prefix=os.environ.get("CRAG_EMBEDDING_QUERY_PREFIX", ""),
            embedding_document_prefix=os.environ.get("CRAG_EMBEDDING_DOCUMENT_PREFIX", ""),
            reranker_api=(_env("CRAG_RERANKER_API", "none") or "none").lower(),
            ocr_engine=(_env("CRAG_OCR_ENGINE", "tesseract") or "tesseract").lower(),
            ocr_languages=_env("CRAG_OCR_LANGUAGES", "fra+eng") or "fra+eng",
            ocr_min_chars=_int("CRAG_OCR_MIN_CHARS", 40),
            ocr_dpi=_int("CRAG_OCR_DPI", 300),
            chunk_max_chars=_int("CRAG_CHUNK_MAX_CHARS", 1800),
            chunk_overlap_chars=_int("CRAG_CHUNK_OVERLAP_CHARS", 200),
            retrieval_top_k=_int("CRAG_RETRIEVAL_TOP_K", 8),
            retrieval_candidates=_int("CRAG_RETRIEVAL_CANDIDATES", 40),
            default_access=(_env("CRAG_DEFAULT_ACCESS", "none") or "none").lower(),
            allowed_hosts=allowed,
            verify_tls=_bool("CRAG_VERIFY_TLS", True),
            ca_bundle=ca_bundle,
            http_trust_env=_bool("CRAG_HTTP_TRUST_ENV", False),
            owui_extensions_dir=Path(_env("CRAG_OWUI_EXTENSIONS_DIR", "./owui_extensions") or "./owui_extensions"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        errors = []
        if self.reranker_api not in {"cohere", "tei", "none"}:
            errors.append("CRAG_RERANKER_API doit valoir cohere, tei ou none")
        if self.ocr_engine not in {"tesseract", "vision", "none"}:
            errors.append("CRAG_OCR_ENGINE doit valoir tesseract, vision ou none")
        if self.default_access not in {"none", "all"}:
            errors.append("CRAG_DEFAULT_ACCESS doit valoir none ou all")
        if not 1 <= self.embedding_dim <= 2000:
            errors.append("CRAG_EMBEDDING_DIM doit être compris entre 1 et 2000 (limite des index HNSW pgvector)")
        if self.chunk_overlap_chars >= self.chunk_max_chars:
            errors.append("CRAG_CHUNK_OVERLAP_CHARS doit être inférieur à CRAG_CHUNK_MAX_CHARS")
        if errors:
            raise ValueError("Configuration invalide : " + " ; ".join(errors))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def reset_settings() -> None:
    """Vide le cache (tests, rechargement de configuration)."""
    get_settings.cache_clear()
