"""Contrats, entreprises, événements contractuels, champs extraits, droits, audit."""

from __future__ import annotations

import json
from typing import Any

from ..domain.parsing import normalize_text
from ..security.authz import AccessScope

_TYPE_ORDER = """
CASE d.type_document
  WHEN 'contrat' THEN 1 WHEN 'cahier_charges' THEN 2 WHEN 'bordereau_prix' THEN 3
  WHEN 'avenant' THEN 4 WHEN 'ordre_service' THEN 5 WHEN 'garantie' THEN 6
  WHEN 'pv_reception_provisoire' THEN 7 WHEN 'levee_reserves' THEN 8
  WHEN 'pv_reception_definitive' THEN 9 WHEN 'mainlevee_garantie' THEN 10 ELSE 20 END
"""


def get_or_create_entreprise(conn, nom: str) -> int | None:
    nom = (nom or "").strip()
    if not nom:
        return None
    row = conn.execute(
        """
        INSERT INTO entreprise (nom, nom_normalise) VALUES (%s, %s)
        ON CONFLICT (nom_normalise) DO UPDATE SET nom = entreprise.nom
        RETURNING id
        """,
        (nom, normalize_text(nom)),
    ).fetchone()
    return row["id"]


def get_by_reference(conn, reference: str) -> dict | None:
    return conn.execute(
        """
        SELECT ct.*, e.nom AS titulaire
          FROM contrat ct LEFT JOIN entreprise e ON e.id = ct.titulaire_id
         WHERE upper(ct.reference) = upper(%s)
        """,
        (reference.strip(),),
    ).fetchone()


def search(conn, text: str, scope: AccessScope, limit: int = 10,
           entreprise: str | None = None) -> list[dict]:
    q = (text or "").strip()
    params: dict[str, Any] = {
        "q": q,
        "like": f"%{q}%",
        "qn": normalize_text(q),
        "ent": f"%{normalize_text(entreprise)}%" if entreprise else None,
        "limit": limit,
        "scope_all": scope.all_contracts,
        "scope_ids": list(scope.contrat_ids),
    }
    return conn.execute(
        """
        WITH scored AS (
          SELECT ct.id, ct.reference, ct.intitule, ct.objet, e.nom AS titulaire,
                 ct.montant_initial_ht, ct.devise, ct.date_signature,
                 GREATEST(
                   word_similarity(unaccent(lower(%(q)s::text)), unaccent(lower(ct.reference))),
                   word_similarity(unaccent(lower(%(q)s::text)),
                                   unaccent(lower(coalesce(ct.intitule,'') || ' ' || coalesce(ct.objet,'')))),
                   coalesce(word_similarity(%(qn)s::text, e.nom_normalise), 0)
                 ) AS score,
                 (SELECT count(*) FROM document d WHERE d.contrat_id = ct.id) AS nb_documents
            FROM contrat ct
            LEFT JOIN entreprise e ON e.id = ct.titulaire_id
           WHERE (%(scope_all)s OR ct.id = ANY(%(scope_ids)s::bigint[]))
             AND (%(ent)s::text IS NULL OR e.nom_normalise LIKE %(ent)s)
        )
        SELECT * FROM scored
         WHERE %(q)s::text = '' OR score >= 0.3 OR reference ILIKE %(like)s
            OR coalesce(intitule,'') ILIKE %(like)s OR coalesce(objet,'') ILIKE %(like)s
         ORDER BY score DESC, reference
         LIMIT %(limit)s
        """,
        params,
    ).fetchall()


def details(conn, contrat_id: int) -> dict:
    contrat = conn.execute(
        """
        SELECT ct.*, e.nom AS titulaire
          FROM contrat ct LEFT JOIN entreprise e ON e.id = ct.titulaire_id
         WHERE ct.id = %s
        """,
        (contrat_id,),
    ).fetchone()
    return {
        "contrat": contrat,
        "parties": conn.execute(
            """
            SELECT e.nom, p.role FROM contrat_partie p JOIN entreprise e ON e.id = p.entreprise_id
             WHERE p.contrat_id = %s ORDER BY p.role, e.nom
            """,
            (contrat_id,),
        ).fetchall(),
        "documents": conn.execute(
            f"""
            SELECT d.id, d.nom_fichier, d.type_document, d.numero_ordre, d.version, d.est_courant,
                   d.date_document, d.nb_pages, d.statut_traitement
              FROM document d WHERE d.contrat_id = %s
             ORDER BY {_TYPE_ORDER}, d.numero_ordre NULLS FIRST, d.version
            """,
            (contrat_id,),
        ).fetchall(),
        "avenants": conn.execute(
            """
            SELECT a.*, d.nom_fichier FROM avenant a LEFT JOIN document d ON d.id = a.document_id
             WHERE a.contrat_id = %s AND a.statut_validation <> 'rejete' ORDER BY a.numero
            """,
            (contrat_id,),
        ).fetchall(),
        "ordres_service": conn.execute(
            """
            SELECT o.*, d.nom_fichier FROM ordre_service o LEFT JOIN document d ON d.id = o.document_id
             WHERE o.contrat_id = %s AND o.statut_validation <> 'rejete'
             ORDER BY o.date_os NULLS LAST, o.numero
            """,
            (contrat_id,),
        ).fetchall(),
        "receptions": conn.execute(
            """
            SELECT r.*, d.nom_fichier FROM reception r LEFT JOIN document d ON d.id = r.document_id
             WHERE r.contrat_id = %s AND r.statut_validation <> 'rejete' ORDER BY r.date_reception
            """,
            (contrat_id,),
        ).fetchall(),
        "champs": conn.execute(
            """
            SELECT DISTINCT ON (c.champ)
                   c.champ, c.valeur_texte, c.valeur_numerique, c.valeur_date, c.unite, c.page,
                   c.section, c.cellule_ref, c.extrait, c.statut, c.source, d.nom_fichier
              FROM champ_extrait c LEFT JOIN document d ON d.id = c.document_id
             WHERE c.contrat_id = %s AND c.statut <> 'rejete'
             ORDER BY c.champ,
                      CASE c.statut WHEN 'corrige' THEN 1 WHEN 'valide' THEN 2 ELSE 3 END,
                      c.created_at DESC
            """,
            (contrat_id,),
        ).fetchall(),
    }


_IMPORTABLE = (
    "intitule", "objet", "maitre_ouvrage", "devise", "montant_initial_ht", "taux_tva",
    "montant_initial_ttc", "date_signature", "date_notification", "date_ods_demarrage",
    "delai_execution_jours", "delai_execution_mois", "statut",
)


def upsert_metadata(conn, reference: str, values: dict[str, Any], titulaire: str | None,
                    acteur: str, source: str = "import") -> int:
    before = get_by_reference(conn, reference)
    contrat_id = before["id"] if before else conn.execute(
        "INSERT INTO contrat (reference) VALUES (%s) RETURNING id", (reference.strip(),)
    ).fetchone()["id"]
    fields = {k: v for k, v in values.items() if k in _IMPORTABLE and v not in (None, "")}
    titulaire_id = get_or_create_entreprise(conn, titulaire) if titulaire else None
    if titulaire_id:
        fields["titulaire_id"] = titulaire_id
        conn.execute(
            """
            INSERT INTO contrat_partie (contrat_id, entreprise_id, role) VALUES (%s, %s, 'titulaire')
            ON CONFLICT DO NOTHING
            """,
            (contrat_id, titulaire_id),
        )
    if fields:
        fields["source_metadonnees"] = source
        assignments = ", ".join(f"{k} = %({k})s" for k in fields)
        conn.execute(
            f"UPDATE contrat SET {assignments}, updated_at = now() WHERE id = %(_id)s",
            {**fields, "_id": contrat_id},
        )
    audit(conn, acteur, "import_metadonnees", "contrat", str(contrat_id),
          _jsonable(before), _jsonable(fields))
    return contrat_id


def audit(conn, acteur: str, action: str, objet: str, objet_id: str | None,
          avant: Any = None, apres: Any = None) -> None:
    conn.execute(
        "INSERT INTO audit_log (acteur, action, objet, objet_id, avant, apres) VALUES (%s,%s,%s,%s,%s,%s)",
        (acteur, action, objet, objet_id,
         json.dumps(avant, ensure_ascii=False, default=str) if avant is not None else None,
         json.dumps(apres, ensure_ascii=False, default=str) if apres is not None else None),
    )


def _jsonable(value):
    if value is None:
        return None
    return {k: (str(v) if v is not None else None) for k, v in dict(value).items()}


# --- Droits ---------------------------------------------------------------------

def grant_access(conn, principal: str, contrat_id: int | None, niveau: str = "lecture") -> None:
    conn.execute(
        """
        INSERT INTO acces_contrat (principal, contrat_id, niveau) VALUES (%s, %s, %s)
        ON CONFLICT (principal, contrat_id) DO UPDATE SET niveau = EXCLUDED.niveau
        """,
        (principal, contrat_id, niveau),
    )


def revoke_access(conn, principal: str, contrat_id: int | None) -> int:
    return conn.execute(
        "DELETE FROM acces_contrat WHERE principal = %s AND contrat_id IS NOT DISTINCT FROM %s",
        (principal, contrat_id),
    ).rowcount


def list_access(conn) -> list[dict]:
    return conn.execute(
        """
        SELECT a.principal, coalesce(ct.reference, '(tous)') AS contrat, a.niveau, a.cree_le
          FROM acces_contrat a LEFT JOIN contrat ct ON ct.id = a.contrat_id
         ORDER BY a.principal, ct.reference NULLS FIRST
        """
    ).fetchall()
