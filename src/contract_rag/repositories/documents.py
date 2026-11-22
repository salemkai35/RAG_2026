"""Documents, pages et chunks."""

from __future__ import annotations

import json
from typing import Iterable

from ..gateways.embeddings import to_pgvector
from ..ingestion.types import ChunkDraft, PageInfo


def upsert_contract_ref(conn, reference: str) -> int:
    row = conn.execute(
        """
        INSERT INTO contrat (reference) VALUES (%s)
        ON CONFLICT (reference) DO UPDATE SET reference = EXCLUDED.reference
        RETURNING id
        """,
        (reference.strip(),),
    ).fetchone()
    return row["id"]


def find_document_by_sha(conn, sha256: str) -> dict | None:
    return conn.execute("SELECT * FROM document WHERE sha256 = %s", (sha256,)).fetchone()


def find_document_by_name(conn, contrat_id: int | None, nom_fichier: str) -> dict | None:
    return conn.execute(
        """
        SELECT * FROM document
        WHERE contrat_id IS NOT DISTINCT FROM %s AND lower(nom_fichier) = lower(%s)
        ORDER BY version DESC LIMIT 1
        """,
        (contrat_id, nom_fichier),
    ).fetchone()


def insert_document(conn, **fields) -> int:
    columns = ", ".join(fields)
    placeholders = ", ".join(f"%({k})s" for k in fields)
    row = conn.execute(
        f"INSERT INTO document ({columns}) VALUES ({placeholders}) RETURNING id", fields
    ).fetchone()
    return row["id"]


def update_document(conn, document_id: int, **fields) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{k} = %({k})s" for k in fields)
    conn.execute(f"UPDATE document SET {assignments} WHERE id = %(_id)s", {**fields, "_id": document_id})


def reset_document_content(conn, document_id: int) -> None:
    conn.execute("DELETE FROM chunk WHERE document_id = %s", (document_id,))
    conn.execute("DELETE FROM document_page WHERE document_id = %s", (document_id,))


def mark_replaced(conn, old_id: int, new_id: int) -> None:
    old = conn.execute("SELECT version FROM document WHERE id = %s", (old_id,)).fetchone()
    conn.execute("UPDATE document SET est_courant = false WHERE id = %s", (old_id,))
    conn.execute(
        "UPDATE document SET remplace_document_id = %s, version = %s WHERE id = %s",
        (old_id, (old["version"] or 1) + 1, new_id),
    )


def set_status(conn, document_id: int, statut: str, message: str | None = None,
               avertissements: list[str] | None = None, nb_pages: int | None = None) -> None:
    conn.execute(
        """
        UPDATE document
           SET statut_traitement = %s, message_erreur = %s,
               avertissements = %s::jsonb, nb_pages = coalesce(%s, nb_pages), traite_le = now()
         WHERE id = %s
        """,
        (statut, message, json.dumps(avertissements or [], ensure_ascii=False), nb_pages, document_id),
    )


def insert_pages(conn, document_id: int, pages: Iterable[PageInfo]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO document_page (document_id, numero, texte, methode, confiance_ocr) VALUES (%s,%s,%s,%s,%s)",
            [(document_id, p.number, p.text.replace("\x00", ""), p.method, p.ocr_confidence) for p in pages],
        )


def insert_chunks(conn, document_id: int, contrat_id: int | None, chunks: list[ChunkDraft],
                  vectors: list[list[float]] | None, model: str | None) -> None:
    rows = []
    for i, c in enumerate(chunks):
        vector = to_pgvector(vectors[i]) if vectors else None
        rows.append((
            document_id, contrat_id, c.ordre, c.type_chunk, c.page_debut, c.page_fin,
            c.section_chemin, c.section_titre, c.table_ref, c.cellule_ref,
            c.contenu.replace("\x00", ""), vector, model if vectors else None,
        ))
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO chunk (document_id, contrat_id, ordre, type_chunk, page_debut, page_fin,
                               section_chemin, section_titre, table_ref, cellule_ref, contenu,
                               embedding, embedding_modele)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s)
            """,
            rows,
        )


def chunks_without_embedding(conn, limit: int) -> list[dict]:
    return conn.execute(
        """
        SELECT c.id, c.contenu, c.section_chemin, d.nom_fichier, d.type_document, ct.reference
          FROM chunk c
          JOIN document d ON d.id = c.document_id
          LEFT JOIN contrat ct ON ct.id = c.contrat_id
         WHERE c.embedding IS NULL
         ORDER BY c.id
         LIMIT %s
        """,
        (limit,),
    ).fetchall()


def update_embeddings(conn, items: list[tuple[int, list[float]]], model: str) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "UPDATE chunk SET embedding = %s::vector, embedding_modele = %s WHERE id = %s",
            [(to_pgvector(vec), model, chunk_id) for chunk_id, vec in items],
        )


def refresh_vectorised_documents(conn) -> int:
    """Passe à 'indexe' les documents dont tous les chunks sont vectorisés."""
    cur = conn.execute(
        """
        UPDATE document d SET statut_traitement = 'indexe', traite_le = now()
         WHERE d.statut_traitement = 'a_vectoriser'
           AND NOT EXISTS (SELECT 1 FROM chunk c WHERE c.document_id = d.id AND c.embedding IS NULL)
        """
    )
    return cur.rowcount


def embedding_column_dim(conn) -> int | None:
    row = conn.execute(
        """
        SELECT atttypmod FROM pg_attribute
         WHERE attrelid = 'chunk'::regclass AND attname = 'embedding'
        """
    ).fetchone()
    return row["atttypmod"] if row and row["atttypmod"] > 0 else None


def recreate_embedding_column(conn, dim: int) -> None:
    conn.execute("DROP INDEX IF EXISTS chunk_embedding_idx")
    conn.execute("UPDATE chunk SET embedding = NULL, embedding_modele = NULL")
    conn.execute(f"ALTER TABLE chunk ALTER COLUMN embedding TYPE vector({int(dim)})")
    conn.execute("CREATE INDEX chunk_embedding_idx ON chunk USING hnsw (embedding vector_cosine_ops)")


def list_documents(conn, contrat_id: int | None = None, limit: int = 200) -> list[dict]:
    return conn.execute(
        """
        SELECT d.id, ct.reference, d.nom_fichier, d.type_document, d.numero_ordre, d.version,
               d.est_courant, d.nb_pages, d.statut_traitement, d.message_erreur,
               jsonb_array_length(d.avertissements) AS nb_avertissements
          FROM document d LEFT JOIN contrat ct ON ct.id = d.contrat_id
         WHERE (%(cid)s::bigint IS NULL OR d.contrat_id = %(cid)s)
         ORDER BY ct.reference NULLS LAST, d.type_document, d.numero_ordre NULLS FIRST, d.version
         LIMIT %(limit)s
        """,
        {"cid": contrat_id, "limit": limit},
    ).fetchall()
