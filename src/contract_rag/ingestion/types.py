"""Représentation intermédiaire commune à tous les formats."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Block:
    """Unité lue dans un document, dans l'ordre de lecture.

    kind : "text" (texte libre, éventuellement multi-lignes), "heading" (titre
    explicite, ex. style Word), "table" (tableau, ``rows`` renseigné).
    """

    kind: str
    text: str = ""
    page: int | None = None
    level: int | None = None
    table_ref: str | None = None
    cell_ref: str | None = None
    rows: list[list[str]] | None = None


@dataclass
class PageInfo:
    number: int
    text: str
    method: str  # texte | ocr:tesseract | ocr:vision | vide
    ocr_confidence: float | None = None


@dataclass
class ParsedDocument:
    blocks: list[Block] = field(default_factory=list)
    pages: list[PageInfo] = field(default_factory=list)
    page_count: int | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class ChunkDraft:
    ordre: int
    type_chunk: str  # texte | tableau
    contenu: str
    page_debut: int | None
    page_fin: int | None
    section_chemin: str | None
    section_titre: str | None
    table_ref: str | None = None
    cellule_ref: str | None = None
