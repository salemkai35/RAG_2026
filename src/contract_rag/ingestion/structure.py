"""Structuration (titres / articles) et découpage en chunks traçables.

Principe : un chunk = un article (ou une partie d'article s'il est long),
avec son chemin de section (« CHAPITRE II > Article 12 – Pénalités »), ses
pages de début/fin, et pour les tableaux leur référence (page/table ou cellules).
"""

from __future__ import annotations

import re

from .types import Block, ChunkDraft

_MAX_HEADING_LEN = 160

_PART_RE = re.compile(
    r"^(TITRE|CHAPITRE|PARTIE|SECTION)\s+([IVXLC]+|\d+|PREMIER|UNIQUE)\b\s*[:\-–—.]?\s*(.*)$",
    re.IGNORECASE,
)
_ARTICLE_RE = re.compile(
    r"^(ARTICLE|Article|ART\.|Art\.)\s*(\d+(?:[.\-]\d+)*|(?i:premier|1er|unique))\b"
    r"\s*(?:[:\-–—.]\s*)?(.*)$"
)
_NUMBERED_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){1,3})\.?\s+([A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ].{2,120})$")


def detect_heading(line: str) -> tuple[int, str] | None:
    """Retourne (niveau, titre) si la ligne est un titre de structure, sinon None."""
    line = line.strip()
    if not line or len(line) > _MAX_HEADING_LEN:
        return None
    match = _PART_RE.match(line)
    if match:
        rest = match.group(3).strip()
        if rest and rest[0].islower():
            return None
        return 0, line
    match = _ARTICLE_RE.match(line)
    if match:
        rest = match.group(3).strip()
        # « Article 12 du présent contrat… » est une phrase, pas un titre.
        if rest and rest[0].islower():
            return None
        if len(rest) > 120 or rest.endswith((",", ";")):
            return None
        numero = match.group(2)
        level = 1 + len(re.findall(r"[.\-]", numero))
        return level, line
    match = _NUMBERED_RE.match(line)
    if match:
        title = match.group(2)
        letters = sum(c.isalpha() for c in title)
        if letters < 0.6 * len(title.replace(" ", "")) or title.rstrip().endswith((".", ",", ";")):
            return None
        level = 1 + match.group(1).count(".")
        return level, line
    return None


def table_to_markdown(rows: list[list[str]]) -> str:
    def clean(cell) -> str:
        return re.sub(r"\s+", " ", str(cell if cell is not None else "")).replace("|", "\\|").strip()

    rows = [[clean(c) for c in row] for row in rows if any(str(c or "").strip() for c in row)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, body = rows[0], rows[1:]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


class _Chunker:
    def __init__(self, max_chars: int, overlap_chars: int):
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars
        self.stack: list[tuple[int, str]] = []
        self.buffer: list[tuple[int | None, str]] = []
        self.chunks: list[ChunkDraft] = []

    # -- sections ------------------------------------------------------------
    def push_heading(self, level: int, title: str) -> None:
        while self.stack and self.stack[-1][0] >= level:
            self.stack.pop()
        self.stack.append((level, title[:_MAX_HEADING_LEN]))

    @property
    def section_path(self) -> str | None:
        return " > ".join(t for _, t in self.stack) or None

    @property
    def section_title(self) -> str | None:
        return self.stack[-1][1] if self.stack else None

    # -- émission ------------------------------------------------------------
    def _emit(self, type_chunk: str, contenu: str, pages: list[int | None], **refs) -> None:
        contenu = contenu.strip()
        if not contenu:
            return
        known = [p for p in pages if p is not None]
        self.chunks.append(
            ChunkDraft(
                ordre=len(self.chunks),
                type_chunk=type_chunk,
                contenu=contenu,
                page_debut=min(known) if known else None,
                page_fin=max(known) if known else None,
                section_chemin=self.section_path,
                section_titre=self.section_title,
                **refs,
            )
        )

    def add_line(self, page: int | None, line: str) -> None:
        if len(line) > self.max_chars:
            for start in range(0, len(line), self.max_chars):
                self.add_line(page, line[start : start + self.max_chars])
            return
        self.buffer.append((page, line))

    def flush(self) -> None:
        if not self.buffer:
            return
        window: list[tuple[int | None, str]] = []
        size = 0
        for page, line in self.buffer:
            if window and size + len(line) + 1 > self.max_chars:
                self._emit("texte", "\n".join(l for _, l in window), [p for p, _ in window])
                # Chevauchement : on reprend les dernières lignes jusqu'à overlap_chars.
                overlap: list[tuple[int | None, str]] = []
                kept = 0
                for item in reversed(window):
                    if kept + len(item[1]) > self.overlap_chars:
                        break
                    overlap.insert(0, item)
                    kept += len(item[1]) + 1
                window, size = overlap, kept
            window.append((page, line))
            size += len(line) + 1
        if window:
            self._emit("texte", "\n".join(l for _, l in window), [p for p, _ in window])
        self.buffer = []

    def add_table(self, block: Block) -> None:
        rows = [r for r in (block.rows or []) if any(str(c or "").strip() for c in r)]
        if not rows:
            return
        header, body = rows[0], rows[1:] or []
        part: list[list[str]] = []
        for row in body:
            candidate = table_to_markdown([header] + part + [row])
            if part and len(candidate) > self.max_chars:
                self._emit("tableau", table_to_markdown([header] + part), [block.page],
                           table_ref=block.table_ref, cellule_ref=block.cell_ref)
                part = []
            part.append(row)
        self._emit("tableau", table_to_markdown([header] + part), [block.page],
                   table_ref=block.table_ref, cellule_ref=block.cell_ref)


def build_chunks(blocks: list[Block], max_chars: int = 1800, overlap_chars: int = 200) -> list[ChunkDraft]:
    chunker = _Chunker(max_chars, overlap_chars)
    for block in blocks:
        if block.kind == "heading":
            chunker.flush()
            chunker.push_heading(block.level if block.level is not None else 1, block.text.strip())
            chunker.add_line(block.page, block.text.strip())
        elif block.kind == "text":
            for raw in block.text.splitlines():
                line = raw.strip()
                if not line:
                    continue
                heading = detect_heading(line)
                if heading:
                    chunker.flush()
                    chunker.push_heading(*heading)
                chunker.add_line(block.page, line)
        elif block.kind == "table":
            # Le texte qui précède est émis d'abord : l'ordre de lecture est conservé.
            chunker.flush()
            chunker.add_table(block)
    chunker.flush()
    return chunker.chunks
