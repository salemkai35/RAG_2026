"""Recherche de passages : hybride SQL puis reranking, avec provenance complète."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..gateways.embeddings import EmbeddingClient
from ..gateways.http import ModelEndpointError
from ..gateways.reranker import RerankerClient
from ..retrieval.hybrid import SearchFilters, hybrid_search
from ..security.authz import UserContext, resolve_scope

log = logging.getLogger(__name__)


@dataclass
class Passage:
    chunk_id: int
    contenu: str
    score: float
    rerank_score: float | None
    document_id: int
    nom_fichier: str
    type_document: str
    numero_ordre: int | None
    version: int
    page_debut: int | None
    page_fin: int | None
    section_chemin: str | None
    table_ref: str | None
    cellule_ref: str | None
    contrat_reference: str | None
    contrat_intitule: str | None

    @property
    def pages(self) -> str | None:
        if self.page_debut is None:
            return None
        if self.page_fin and self.page_fin != self.page_debut:
            return f"p. {self.page_debut}-{self.page_fin}"
        return f"p. {self.page_debut}"

    @property
    def source_label(self) -> str:
        parts = [self.nom_fichier]
        if self.version and self.version > 1:
            parts.append(f"v{self.version}")
        if self.pages:
            parts.append(self.pages)
        if self.cellule_ref:
            parts.append(self.cellule_ref)
        elif self.table_ref:
            parts.append(f"tableau {self.table_ref}")
        return ", ".join(parts)


@dataclass
class SearchResult:
    passages: list[Passage]
    warnings: list[str] = field(default_factory=list)
    acces_refuse: bool = False


class SearchService:
    def __init__(self, settings: Settings | None = None, embedder: EmbeddingClient | None = None,
                 reranker: RerankerClient | None = None):
        self.settings = settings or get_settings()
        self.embedder = embedder or EmbeddingClient(self.settings)
        self.reranker = reranker or RerankerClient(self.settings)

    def search_passages(self, conn, question: str, user: UserContext,
                        filters: SearchFilters | None = None, top_k: int | None = None) -> SearchResult:
        filters = filters or SearchFilters()
        top_k = top_k or self.settings.retrieval_top_k
        warnings: list[str] = []
        scope = resolve_scope(conn, user, self.settings.default_access)
        if scope.is_empty:
            return SearchResult([], ["Aucun contrat n'est accessible pour cet utilisateur."], acces_refuse=True)

        query_vector = None
        try:
            query_vector = self.embedder.embed_query(question)
        except ModelEndpointError as exc:
            log.warning("Embeddings indisponibles, recherche lexicale seule : %s", exc)
            warnings.append("Service d'embeddings indisponible : recherche par mots-clés uniquement.")

        rows = hybrid_search(conn, question, query_vector, filters, scope, self.settings.retrieval_candidates)
        if not rows:
            return SearchResult([], warnings)

        order: list[tuple[int, float | None]]
        try:
            order = self.reranker.rerank(question, [r["contenu"] for r in rows], top_k)
        except ModelEndpointError as exc:
            log.warning("Reranker indisponible : %s", exc)
            warnings.append("Reranker indisponible : classement hybride sans reranking.")
            order = [(i, None) for i in range(min(top_k, len(rows)))]

        passages = []
        for index, rerank_score in order:
            r = rows[index]
            passages.append(Passage(
                chunk_id=r["chunk_id"], contenu=r["contenu"], score=float(r["score"]),
                rerank_score=rerank_score, document_id=r["document_id"], nom_fichier=r["nom_fichier"],
                type_document=r["type_document"], numero_ordre=r["numero_ordre"], version=r["version"],
                page_debut=r["page_debut"], page_fin=r["page_fin"], section_chemin=r["section_chemin"],
                table_ref=r["table_ref"], cellule_ref=r["cellule_ref"],
                contrat_reference=r["contrat_reference"], contrat_intitule=r["contrat_intitule"],
            ))
        return SearchResult(passages, warnings)
