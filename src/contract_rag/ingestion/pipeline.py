"""Orchestration de l'ingestion : idempotente (empreinte SHA-256), document par document."""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..domain.parsing import parse_date, parse_int
from ..gateways.http import ModelEndpointError
from ..repositories import documents as docs
from .classify import DOCUMENT_TYPES, classify_filename
from .loaders import LEGACY_EXTENSIONS, SUPPORTED_EXTENSIONS, parse_file
from .storage import sha256_file, store_original
from .structure import build_chunks

log = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.csv"
_IGNORED_NAMES = {MANIFEST_NAME, "readme.md", "thumbs.db", ".ds_store"}


@dataclass
class ManifestEntry:
    type_document: str | None = None
    numero_ordre: int | None = None
    date_document: object = None
    remplace: str | None = None


@dataclass
class IngestOutcome:
    path: str
    status: str  # indexe | a_vectoriser | ignore | erreur
    document_id: int | None = None
    chunks: int = 0
    message: str = ""
    warnings: list[str] = field(default_factory=list)


def read_manifest(folder: Path) -> dict[str, ManifestEntry]:
    path = folder / MANIFEST_NAME
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    if not lines:
        return {}
    delimiter = max(";,\t", key=lines[0].count)
    entries: dict[str, ManifestEntry] = {}
    for row in csv.DictReader(lines, delimiter=delimiter):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        name = row.get("fichier")
        if not name:
            continue
        doc_type = row.get("type_document") or None
        if doc_type and doc_type not in DOCUMENT_TYPES:
            raise ValueError(f"{path}: type_document inconnu « {doc_type} » pour {name}")
        entries[name.lower()] = ManifestEntry(
            type_document=doc_type,
            numero_ordre=parse_int(row.get("numero_ordre")),
            date_document=parse_date(row.get("date_document")),
            remplace=row.get("remplace") or None,
        )
    return entries


def _is_candidate(path: Path) -> bool:
    name = path.name.lower()
    if name in _IGNORED_NAMES or name.startswith(("~$", ".")):
        return False
    ext = path.suffix.lower()
    return ext in SUPPORTED_EXTENSIONS or ext in LEGACY_EXTENSIONS


def embedding_text(chunk, contrat_ref: str | None, type_document: str, nom_fichier: str) -> str:
    """Texte vectorisé = contexte (contrat, type, section) + contenu du chunk."""
    header = " | ".join(x for x in (f"Contrat {contrat_ref}" if contrat_ref else None,
                                    type_document.replace("_", " "), nom_fichier) if x)
    section = chunk.section_chemin or ""
    return f"{header}\n{section}\n{chunk.contenu}".strip()


class IngestionPipeline:
    def __init__(self, conn, settings: Settings, embedder=None, ocr_engine=None):
        self.conn = conn
        self.settings = settings
        self.embedder = embedder
        self.ocr_engine = ocr_engine

    # -- parcours -------------------------------------------------------------
    def ingest_path(self, path: Path, contrat_ref: str | None = None, force: bool = False) -> list[IngestOutcome]:
        path = Path(path)
        if path.is_file():
            manifest = read_manifest(path.parent)
            return [self._safe_ingest(path, contrat_ref, manifest.get(path.name.lower()), force)]
        outcomes: list[IngestOutcome] = []
        if contrat_ref:
            outcomes += self._ingest_folder(path, contrat_ref, force, recursive=True)
            return outcomes
        # Fichiers à la racine : sans contrat ; chaque sous-dossier = un contrat.
        outcomes += self._ingest_folder(path, None, force, recursive=False)
        for sub in sorted(p for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")):
            outcomes += self._ingest_folder(sub, sub.name, force, recursive=True)
        return outcomes

    def _ingest_folder(self, folder: Path, contrat_ref: str | None, force: bool, recursive: bool) -> list[IngestOutcome]:
        manifest = read_manifest(folder)
        files = sorted(folder.rglob("*") if recursive else folder.iterdir())
        outcomes = []
        for file in files:
            if file.is_file() and _is_candidate(file):
                outcomes.append(self._safe_ingest(file, contrat_ref, manifest.get(file.name.lower()), force))
        return outcomes

    def _safe_ingest(self, path: Path, contrat_ref: str | None, entry: ManifestEntry | None,
                     force: bool) -> IngestOutcome:
        """Un fichier en échec (illisible, base momentanément indisponible…) n'arrête pas le lot."""
        try:
            return self.ingest_file(path, contrat_ref, entry, force)
        except Exception as exc:
            log.exception("Échec d'ingestion de %s", path)
            return IngestOutcome(str(path), "erreur", message=f"{type(exc).__name__}: {exc}")

    # -- un document ----------------------------------------------------------
    def ingest_file(self, path: Path, contrat_ref: str | None, entry: ManifestEntry | None = None,
                    force: bool = False) -> IngestOutcome:
        conn, settings = self.conn, self.settings
        sha = sha256_file(path)
        existing = docs.find_document_by_sha(conn, sha)
        if existing and existing["statut_traitement"] in ("indexe", "a_vectoriser") and not force:
            return IngestOutcome(str(path), "ignore", existing["id"], message="déjà indexé (même empreinte)")

        classification = classify_filename(path.name)
        doc_type = (entry.type_document if entry and entry.type_document else classification.type_document)
        numero = entry.numero_ordre if entry and entry.numero_ordre is not None else classification.numero_ordre
        stored = store_original(settings.storage_root, path, sha)

        with conn.transaction():
            contrat_id = docs.upsert_contract_ref(conn, contrat_ref) if contrat_ref else None
            fields = dict(
                contrat_id=contrat_id, nom_fichier=path.name, chemin_source=str(path),
                chemin_stockage=stored, extension=path.suffix.lower(), taille_octets=path.stat().st_size,
                type_document=doc_type, numero_ordre=numero,
                date_document=entry.date_document if entry else None,
            )
            if existing:
                document_id = existing["id"]
                docs.reset_document_content(conn, document_id)
                docs.update_document(conn, document_id, **fields, statut_traitement="importe")
            else:
                document_id = docs.insert_document(conn, sha256=sha, **fields)
            if entry and entry.remplace:
                previous = docs.find_document_by_name(conn, contrat_id, entry.remplace)
                if previous and previous["id"] != document_id:
                    docs.mark_replaced(conn, previous["id"], document_id)

        try:
            parsed = parse_file(path, settings, self.ocr_engine)
            chunks = build_chunks(parsed.blocks, settings.chunk_max_chars, settings.chunk_overlap_chars)
            warnings = list(parsed.warnings)
            if not chunks:
                warnings.append("Aucun contenu exploitable extrait.")
            vectors, status, model = None, "a_vectoriser", None
            if self.embedder is not None and chunks:
                try:
                    vectors = self.embedder.embed_documents(
                        [embedding_text(c, contrat_ref, doc_type, path.name) for c in chunks]
                    )
                    status, model = "indexe", self.embedder.model_name
                except ModelEndpointError as exc:
                    warnings.append(f"Vectorisation reportée : {exc}")
            elif not chunks:
                status = "indexe"
            with conn.transaction():
                docs.insert_pages(conn, document_id, parsed.pages)
                docs.insert_chunks(conn, document_id, contrat_id, chunks, vectors, model)
                docs.set_status(conn, document_id, status, None, warnings, parsed.page_count)
            return IngestOutcome(str(path), status, document_id, len(chunks), warnings=warnings)
        except Exception as exc:  # le lot continue ; l'erreur est tracée sur le document
            log.exception("Échec d'ingestion de %s", path)
            with conn.transaction():
                docs.set_status(conn, document_id, "erreur", f"{type(exc).__name__}: {exc}")
            return IngestOutcome(str(path), "erreur", document_id, message=str(exc))


def vectorise_pending(conn, embedder, batch_size: int = 64) -> int:
    """Calcule les embeddings manquants (endpoint indisponible lors de l'ingestion, réindexation)."""
    total = 0
    while True:
        rows = docs.chunks_without_embedding(conn, batch_size)
        if not rows:
            break
        from ..ingestion.types import ChunkDraft

        texts = []
        for r in rows:
            draft = ChunkDraft(0, "texte", r["contenu"], None, None, r["section_chemin"], None)
            texts.append(embedding_text(draft, r["reference"], r["type_document"], r["nom_fichier"]))
        vectors = embedder.embed_documents(texts)
        with conn.transaction():
            docs.update_embeddings(conn, [(r["id"], v) for r, v in zip(rows, vectors)], embedder.model_name)
        total += len(rows)
        log.info("%s chunks vectorisés", total)
    with conn.transaction():
        docs.refresh_vectorised_documents(conn)
    return total
