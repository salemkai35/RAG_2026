"""Recherche hybride en une requête SQL : plein texte français + vecteurs, fusion RRF.

* lexical  : tsvector 'fr_unaccent' (racinisation française, insensible aux accents),
             requête en OU pondérée par ts_rank_cd ;
* sémantique : distance cosinus pgvector ;
* fusion   : Reciprocal Rank Fusion  score = Σ 1 / (k + rang) ;
* filtres  : contrat, type de document, titulaire, dates, montants, périmètre d'accès.

Pour quelques centaines de contrats (dizaines de milliers de chunks), la recherche
vectorielle filtrée reste exacte et rapide ; aucun moteur externe n'est nécessaire.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from ..domain.parsing import normalize_text
from ..gateways.embeddings import to_pgvector
from ..security.authz import AccessScope

RRF_K = 60
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass
class SearchFilters:
    contrat_refs: list[str] | None = None
    types_document: list[str] | None = None
    entreprise: str | None = None
    date_signature_min: date | None = None
    date_signature_max: date | None = None
    montant_ht_min: Decimal | None = None
    montant_ht_max: Decimal | None = None
    inclure_versions_remplacees: bool = False


def build_tsquery(question: str) -> str | None:
    """« Quel est le délai de l'avenant 2 ? » -> 'quel | est | le | delai | ...' (stop-words retirés par PG)."""
    words = [w.replace("_", "") for w in _WORD.findall(normalize_text(question))]
    # Les nombres sont conservés même courts (« avenant n°2 » -> 2).
    words = list(dict.fromkeys(w for w in words if len(w) >= 2 or w.isdigit()))[:30]
    return " | ".join(words) if words else None


def _where(filters: SearchFilters, scope: AccessScope, params: dict[str, Any]) -> str:
    conds = ["d.statut_traitement IN ('indexe', 'a_vectoriser')"]
    if not filters.inclure_versions_remplacees:
        conds.append("d.est_courant")
    if not scope.all_contracts:
        conds.append("c.contrat_id = ANY(%(scope_ids)s::bigint[])")
        params["scope_ids"] = list(scope.contrat_ids)
    if filters.contrat_refs:
        conds.append("upper(ct.reference) = ANY(%(refs)s::text[])")
        params["refs"] = [r.strip().upper() for r in filters.contrat_refs]
    if filters.types_document:
        conds.append("d.type_document = ANY(%(types)s::text[])")
        params["types"] = filters.types_document
    if filters.entreprise:
        conds.append(
            "EXISTS (SELECT 1 FROM contrat_partie p JOIN entreprise e ON e.id = p.entreprise_id "
            "WHERE p.contrat_id = ct.id AND e.nom_normalise LIKE %(ent)s)"
        )
        params["ent"] = f"%{normalize_text(filters.entreprise)}%"
    if filters.date_signature_min:
        conds.append("ct.date_signature >= %(dmin)s")
        params["dmin"] = filters.date_signature_min
    if filters.date_signature_max:
        conds.append("ct.date_signature <= %(dmax)s")
        params["dmax"] = filters.date_signature_max
    if filters.montant_ht_min is not None:
        conds.append("ct.montant_initial_ht >= %(mmin)s")
        params["mmin"] = filters.montant_ht_min
    if filters.montant_ht_max is not None:
        conds.append("ct.montant_initial_ht <= %(mmax)s")
        params["mmax"] = filters.montant_ht_max
    return " AND ".join(conds)


def hybrid_search(conn, question: str, query_vector: list[float] | None, filters: SearchFilters,
                  scope: AccessScope, limit: int = 40) -> list[dict]:
    if scope.is_empty:
        return []
    params: dict[str, Any] = {"k": limit, "rrf_k": RRF_K}
    where = _where(filters, scope, params)
    tsquery = build_tsquery(question)

    ranked = []
    if query_vector is not None:
        params["qvec"] = to_pgvector(query_vector)
        ranked.append(
            """
            SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> %(qvec)s::vector) AS rang, 'vec' AS voie
              FROM chunk c WHERE c.id IN (SELECT id FROM base) AND c.embedding IS NOT NULL
             ORDER BY c.embedding <=> %(qvec)s::vector LIMIT %(k)s
            """
        )
    if tsquery:
        params["tsq"] = tsquery
        ranked.append(
            """
            SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.tsv, q) DESC) AS rang, 'lex' AS voie
              FROM chunk c, to_tsquery('fr_unaccent'::regconfig, %(tsq)s) AS q
             WHERE c.id IN (SELECT id FROM base) AND c.tsv @@ q
             ORDER BY ts_rank_cd(c.tsv, q) DESC LIMIT %(k)s
            """
        )
    if not ranked:
        return []
    union = " UNION ALL ".join(f"({part})" for part in ranked)
    sql = f"""
        WITH base AS (
            SELECT c.id
              FROM chunk c
              JOIN document d ON d.id = c.document_id
              LEFT JOIN contrat ct ON ct.id = c.contrat_id
             WHERE {where}
        ),
        ranked AS ({union}),
        fused AS (
            SELECT id,
                   sum(1.0 / (%(rrf_k)s + rang)) AS score,
                   min(rang) FILTER (WHERE voie = 'vec') AS rang_vec,
                   min(rang) FILTER (WHERE voie = 'lex') AS rang_lex
              FROM ranked GROUP BY id
        )
        SELECT f.score, f.rang_vec, f.rang_lex,
               c.id AS chunk_id, c.contenu, c.type_chunk, c.page_debut, c.page_fin,
               c.section_chemin, c.table_ref, c.cellule_ref,
               d.id AS document_id, d.nom_fichier, d.type_document, d.numero_ordre, d.version,
               d.chemin_stockage,
               ct.id AS contrat_id, ct.reference AS contrat_reference, ct.intitule AS contrat_intitule
          FROM fused f
          JOIN chunk c ON c.id = f.id
          JOIN document d ON d.id = c.document_id
          LEFT JOIN contrat ct ON ct.id = c.contrat_id
         ORDER BY f.score DESC
         LIMIT %(k)s
    """
    return conn.execute(sql, params).fetchall()
