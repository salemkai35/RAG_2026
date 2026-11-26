"""Interface en ligne de commande : contract-rag <commande>.

Dans le déploiement Docker : docker compose run --rm ingest <commande>
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

from .config import get_settings


def _print_table(rows: list[dict], columns: list[str] | None = None) -> None:
    if not rows:
        print("(aucun résultat)")
        return
    columns = columns or list(rows[0].keys())
    widths = {c: min(60, max(len(c), *(len(str(r.get(c, "") if r.get(c) is not None else "")) for r in rows)))
              for c in columns}
    print("  ".join(c.ljust(widths[c]) for c in columns))
    print("  ".join("-" * widths[c] for c in columns))
    for r in rows:
        print("  ".join(str(r.get(c) if r.get(c) is not None else "")[: widths[c]].ljust(widths[c]) for c in columns))


# --- db ---------------------------------------------------------------------------

def cmd_db(args) -> int:
    from .db import connect
    from .db import migrate

    with connect() as conn:
        if args.action == "upgrade":
            applied = migrate.upgrade(conn)
            print("Migrations appliquées : " + (", ".join(applied) if applied else "aucune (à jour)"))
        else:
            for version, done in migrate.status(conn):
                print(f"{'✅' if done else '⏳'} {version}")
    return 0


# --- check ------------------------------------------------------------------------

def _sample_page():
    """Petite image de test pour l'OCR Vision (texte connu)."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (900, 220), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=56)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    draw.text((30, 30), "Contrat IAP-2026-117", fill="black", font=font)
    draw.text((30, 120), "Montant : 125 000 DA", fill="black", font=font)
    return image


def cmd_check(args) -> int:
    import dataclasses

    base = get_settings()
    # Contrôle : un seul essai par endpoint, délai borné (CRAG_CHECK_TIMEOUT, 60 s par défaut).
    limit = float(os.environ.get("CRAG_CHECK_TIMEOUT", "60"))
    settings = dataclasses.replace(
        base, **{role: dataclasses.replace(getattr(base, role), timeout=min(getattr(base, role).timeout, limit))
                 for role in ("llm", "embedding", "reranker", "vision")}
    )
    results: list[tuple[str, str, str]] = []

    def record(name: str, func, optional: bool = False) -> None:
        print(f"…  {name:<20}", end="\r", flush=True)
        try:
            detail = func()
            results.append(("✅", name, detail or ""))
        except Exception as exc:  # noqa: BLE001 – diagnostic
            results.append(("⚠️ " if optional else "❌", name, f"{type(exc).__name__}: {exc}"))
        icon, label, detail = results[-1]
        print(f"{icon} {label:<20} {detail}", flush=True)

    def check_db() -> str:
        from .db import connect
        from .db import migrate

        with connect() as conn:
            version = conn.execute("SHOW server_version").fetchone()["server_version"]
            exts = {r["extname"]: r["extversion"] for r in conn.execute("SELECT extname, extversion FROM pg_extension")}
            missing = [e for e in ("vector", "unaccent", "pg_trgm") if e not in exts]
            if missing:
                raise RuntimeError("extensions manquantes : " + ", ".join(missing))
            pending = [v for v, done in migrate.status(conn) if not done]
            if pending:
                raise RuntimeError("migrations non appliquées : " + ", ".join(pending) + " (contract-rag db upgrade)")
            return f"PostgreSQL {version}, pgvector {exts['vector']}"

    def check_storage() -> str:
        root = settings.storage_root
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".probe"
        probe.write_text("ok")
        probe.unlink()
        return str(root)

    def check_llm() -> str:
        from .gateways.llm import LLMClient

        answer = LLMClient(settings, retries=1).chat([{"role": "user", "content": "Réponds uniquement : OK"}], max_tokens=64)
        if not answer.strip():
            raise RuntimeError(f"{settings.llm.model} a répondu vide (modèle « thinking » : désactiver le raisonnement, "
                               "voir DEPLOIEMENT_ROCKY_OLLAMA.md)")
        return f"{settings.llm.model} @ {settings.llm.base_url} → « {answer.strip()[:30]} »"

    def check_embeddings() -> str:
        from .gateways.embeddings import EmbeddingClient

        vector = EmbeddingClient(settings, retries=1).embed_query("contrat de travaux")
        return f"{settings.embedding.model} @ {settings.embedding.base_url}, dimension {len(vector)}"

    def check_reranker() -> str:
        from .gateways.reranker import RerankerClient

        client = RerankerClient(settings, retries=1)
        if not client.enabled:
            return "désactivé (RERANKER_API=none ou URL vide)"
        order = client.rerank("pénalités de retard", ["Article 12 – Pénalités de retard", "Article 3 – Objet"], 2)
        return f"{settings.reranker.model} ({settings.reranker_api}), meilleur document : #{order[0][0]}"

    def check_ocr() -> str:
        if settings.ocr_engine == "tesseract":
            import pytesseract

            langs = set(pytesseract.get_languages(config=""))
            wanted = set(settings.ocr_languages.split("+"))
            if wanted - langs:
                raise RuntimeError("langues Tesseract manquantes : " + ", ".join(sorted(wanted - langs)))
            return f"Tesseract {pytesseract.get_tesseract_version()} ({settings.ocr_languages})"
        if settings.ocr_engine == "vision":
            from .gateways.http import ensure_configured

            ensure_configured(settings.vision, settings)
            from .gateways.ocr import VisionOcr

            text = VisionOcr(settings)(_sample_page()).text
            found = "IAP-2026-117" in text.replace(" ", "")
            verdict = "lecture exacte" if found else f"lecture approximative : « {text.strip()[:40]} »"
            return f"Vision {settings.vision.model} @ {settings.vision.base_url} → {verdict}"
        return "désactivé (OCR_ENGINE=none)"

    record("Base contrats", check_db)
    record("Stockage documents", check_storage)
    record("LLM", check_llm)
    record("Embeddings", check_embeddings)
    record("Reranker", check_reranker, optional=True)
    record("OCR", check_ocr, optional=True)
    if settings.allowed_hosts:
        print(f"ℹ️  {'Hôtes IA autorisés':<20} {', '.join(settings.allowed_hosts)}")
    return 1 if any(icon == "❌" for icon, _, _ in results) else 0


# --- ingest / reindex -------------------------------------------------------------

def cmd_ingest(args) -> int:
    from .db import connect
    from .gateways.embeddings import EmbeddingClient
    from .gateways.ocr import get_ocr_engine
    from .ingestion.pipeline import IngestionPipeline

    settings = get_settings()
    embedder = None if args.no_embed else EmbeddingClient(settings)
    with connect() as conn:
        pipeline = IngestionPipeline(conn, settings, embedder, get_ocr_engine(settings))
        outcomes = pipeline.ingest_path(Path(args.path), args.contrat, args.force)
    counts: dict[str, int] = {}
    for o in outcomes:
        counts[o.status] = counts.get(o.status, 0) + 1
        icon = {"indexe": "✅", "a_vectoriser": "🟡", "ignore": "⏭ ", "erreur": "❌"}[o.status]
        extra = f"{o.chunks} chunks" if o.chunks else o.message
        print(f"{icon} {Path(o.path).name}  {extra}")
        for w in o.warnings:
            print(f"     ⚠️  {w}")
    print("Bilan : " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) if counts else "Aucun fichier.")
    return 1 if counts.get("erreur") else 0


def cmd_reindex(args) -> int:
    from .db import connect
    from .gateways.embeddings import EmbeddingClient
    from .ingestion.pipeline import vectorise_pending
    from .repositories import documents as docs

    settings = get_settings()
    with connect() as conn:
        if args.all:
            current = docs.embedding_column_dim(conn)
            with conn.transaction():
                if current != settings.embedding_dim:
                    print(f"Dimension {current} → {settings.embedding_dim} : recréation de la colonne et de l'index.")
                    docs.recreate_embedding_column(conn, settings.embedding_dim)
                else:
                    conn.execute("UPDATE chunk SET embedding = NULL, embedding_modele = NULL")
                conn.execute("UPDATE document SET statut_traitement = 'a_vectoriser' WHERE statut_traitement = 'indexe'")
        total = vectorise_pending(conn, EmbeddingClient(settings))
    print(f"{total} chunks vectorisés.")
    return 0


# --- contrats / documents / recherche -----------------------------------------------

_CSV_FIELDS = ("intitule", "objet", "maitre_ouvrage", "devise", "montant_initial_ht", "taux_tva",
               "montant_initial_ttc", "date_signature", "date_notification", "date_ods_demarrage",
               "delai_execution_jours", "delai_execution_mois", "statut")


def cmd_contracts(args) -> int:
    from .db import connect
    from .domain.parsing import parse_date, parse_decimal, parse_int
    from .repositories import contracts as repo
    from .security.authz import AccessScope

    with connect() as conn:
        if args.action == "import":
            lines = Path(args.file).read_text(encoding="utf-8-sig").splitlines()
            delimiter = max(";,\t", key=lines[0].count)
            count = 0
            with conn.transaction():
                for row in csv.DictReader(lines, delimiter=delimiter):
                    row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                    if not row.get("reference"):
                        continue
                    values = {}
                    for key in _CSV_FIELDS:
                        raw = row.get(key)
                        if key.startswith("montant") or key == "taux_tva":
                            values[key] = parse_decimal(raw)
                        elif key.startswith("date"):
                            values[key] = parse_date(raw)
                        elif key.startswith("delai"):
                            values[key] = parse_int(raw)
                        else:
                            values[key] = raw or None
                    repo.upsert_metadata(conn, row["reference"], values, row.get("titulaire"),
                                         acteur=os.environ.get("USER", "cli"), source="import")
                    count += 1
            print(f"{count} contrats importés / mis à jour.")
        else:
            rows = repo.search(conn, args.q or "", AccessScope(all_contracts=True), limit=args.limit)
            _print_table(rows, ["reference", "intitule", "titulaire", "montant_initial_ht", "date_signature", "nb_documents"])
    return 0


def cmd_documents(args) -> int:
    from .db import connect
    from .repositories import contracts as repo
    from .repositories import documents as docs

    with connect() as conn:
        contrat_id = None
        if args.contrat:
            contrat = repo.get_by_reference(conn, args.contrat)
            if not contrat:
                print(f"Contrat {args.contrat} inconnu.")
                return 1
            contrat_id = contrat["id"]
        _print_table(docs.list_documents(conn, contrat_id),
                     ["id", "reference", "nom_fichier", "type_document", "numero_ordre", "version",
                      "nb_pages", "statut_traitement", "nb_avertissements"])
    return 0


def cmd_search(args) -> int:
    from .db import connect
    from .presentation import passages_markdown
    from .retrieval.hybrid import SearchFilters
    from .security.authz import SYSTEM_USER
    from .services.search_service import SearchService

    filters = SearchFilters(contrat_refs=[args.contrat] if args.contrat else None,
                            types_document=[args.type] if args.type else None)
    with connect() as conn:
        result = SearchService().search_passages(conn, args.question, SYSTEM_USER, filters, args.k)
    print(passages_markdown(result.passages, result.warnings))
    if args.debug:
        for p in result.passages:
            print(f"chunk={p.chunk_id} rrf={p.score:.4f} rerank={p.rerank_score}")
    return 0


def cmd_show(args) -> int:
    from .db import connect
    from .presentation import contract_details_markdown
    from .security.authz import SYSTEM_USER
    from .services.contract_service import ContractService

    service = ContractService()
    with connect() as conn:
        lookup = service.resolve(conn, args.reference, SYSTEM_USER)
        if not lookup.contrat:
            print(f"Contrat {args.reference} introuvable.")
            return 1
        print(contract_details_markdown(service.details(conn, lookup.contrat["id"])))
    return 0


# --- droits -------------------------------------------------------------------------

def cmd_access(args) -> int:
    from .db import connect
    from .repositories import contracts as repo

    with connect() as conn:
        if args.action == "list":
            _print_table(repo.list_access(conn))
            return 0
        contrat_id = None
        if args.contrat:
            contrat = repo.get_by_reference(conn, args.contrat)
            if not contrat:
                print(f"Contrat {args.contrat} inconnu.")
                return 1
            contrat_id = contrat["id"]
        with conn.transaction():
            if args.action == "grant":
                repo.grant_access(conn, args.principal, contrat_id, args.niveau)
                repo.audit(conn, os.environ.get("USER", "cli"), "grant", "acces_contrat", args.principal,
                           None, {"contrat": args.contrat or "*", "niveau": args.niveau})
                print(f"Accès {args.niveau} accordé à {args.principal} sur {args.contrat or 'tous les contrats'}.")
            else:
                n = repo.revoke_access(conn, args.principal, contrat_id)
                repo.audit(conn, os.environ.get("USER", "cli"), "revoke", "acces_contrat", args.principal,
                           {"contrat": args.contrat or "*"}, None)
                print(f"{n} règle(s) supprimée(s).")
    return 0


# --- Open WebUI ---------------------------------------------------------------------

def cmd_owui(args) -> int:
    from .owui.deploy import deploy

    api_key = args.api_key or os.environ.get("OWUI_API_KEY")
    if not api_key:
        print("Clé API Open WebUI requise (--api-key ou OWUI_API_KEY) : Paramètres > Compte > Clés API.")
        return 1
    url = args.url or os.environ.get("OWUI_INTERNAL_URL", "http://localhost:3000")
    directory = Path(args.dir) if args.dir else get_settings().owui_extensions_dir
    results = deploy(url, api_key, directory)
    for r in results:
        print(f"{'✅' if r.ok else '❌'} {r.kind}/{r.id} ({r.action}) {r.message}")
    if not results:
        print(f"Aucune extension trouvée dans {directory}.")
    return 0 if results and all(r.ok for r in results) else 1


# --- parseur ------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="contract-rag", description="Outils métier Contrats RAG (IAP)")
    parser.add_argument("-v", "--verbose", action="store_true", help="journalisation détaillée")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("db", help="migrations du schéma métier")
    p.add_argument("action", choices=["upgrade", "status"])
    p.set_defaults(func=cmd_db)

    p = sub.add_parser("check", help="contrôle de la base, du stockage et des endpoints modèles")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("ingest", help="ingérer un fichier ou un dossier (un sous-dossier par contrat)")
    p.add_argument("path")
    p.add_argument("--contrat", help="référence du contrat (sinon : nom du sous-dossier)")
    p.add_argument("--force", action="store_true", help="retraiter même si déjà indexé")
    p.add_argument("--no-embed", action="store_true", help="ne pas vectoriser (reindex plus tard)")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("reindex", help="vectoriser les chunks manquants (--all : tout recalculer)")
    p.add_argument("--all", action="store_true", help="après changement de modèle d'embedding")
    p.set_defaults(func=cmd_reindex)

    p = sub.add_parser("contracts", help="référentiel des contrats")
    p.add_argument("action", choices=["import", "list"])
    p.add_argument("file", nargs="?", help="CSV à importer (voir docs/IMPORT_CONTRATS.md)")
    p.add_argument("--q", help="filtre texte (list)")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_contracts)

    p = sub.add_parser("documents", help="lister les documents et leur statut")
    p.add_argument("--contrat")
    p.set_defaults(func=cmd_documents)

    p = sub.add_parser("search", help="tester la recherche hybride")
    p.add_argument("question")
    p.add_argument("--contrat")
    p.add_argument("--type")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--debug", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("show", help="afficher la fiche d'un contrat")
    p.add_argument("reference")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("access", help="droits d'accès métier aux contrats")
    p.add_argument("action", choices=["grant", "revoke", "list"])
    p.add_argument("principal", nargs="?", help="'*', 'user:<email>' ou 'group:<nom>'")
    p.add_argument("--contrat", help="référence (absent = tous les contrats)")
    p.add_argument("--niveau", choices=["lecture", "correction"], default="lecture")
    p.set_defaults(func=cmd_access)

    p = sub.add_parser("owui", help="publier les Tools / Functions dans Open WebUI")
    p.add_argument("action", choices=["deploy"])
    p.add_argument("--url")
    p.add_argument("--api-key")
    p.add_argument("--dir")
    p.set_defaults(func=cmd_owui)

    p = sub.add_parser("ocr-bridge", help="passerelle d'extraction (PDF scannés, images) pour Open WebUI")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8090)
    p.set_defaults(func=cmd_ocr_bridge)
    return parser


def cmd_ocr_bridge(args) -> int:
    from .bridge import serve

    logging.getLogger("contract_rag").setLevel(logging.INFO)
    serve(args.host, args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.command == "contracts" and args.action == "import" and not args.file:
        parser.error("contracts import : fichier CSV requis")
    if args.command == "access" and args.action in ("grant", "revoke") and not args.principal:
        parser.error("access grant/revoke : principal requis")
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # message clair pour l'exploitant
        if args.verbose:
            raise
        print(f"❌ {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
