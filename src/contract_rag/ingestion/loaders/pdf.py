"""PDF textuels et numérisés : texte + tableaux par page, OCR des pages sans texte."""

from __future__ import annotations

import logging
from pathlib import Path

from ...config import Settings
from ..types import Block, PageInfo, ParsedDocument

log = logging.getLogger(__name__)


def _outside(bboxes):
    def keep(obj) -> bool:
        if obj.get("object_type") != "char":
            return True
        cx = (obj["x0"] + obj["x1"]) / 2
        cy = (obj["top"] + obj["bottom"]) / 2
        return not any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in bboxes)

    return keep


def parse_pdf(path: Path, settings: Settings, ocr_engine=None) -> ParsedDocument:
    import pdfplumber

    result = ParsedDocument()
    pdfium_doc = None
    try:
        with pdfplumber.open(str(path)) as pdf:
            result.page_count = len(pdf.pages)
            for number, page in enumerate(pdf.pages, start=1):
                tables: list[Block] = []
                try:
                    found = page.find_tables()
                except Exception as exc:  # pdfplumber peut échouer sur des pages exotiques
                    log.debug("Détection de tableaux impossible p.%s : %s", number, exc)
                    found = []
                for index, table in enumerate(found, start=1):
                    rows = [[(c or "").strip() for c in row] for row in table.extract()]
                    if rows and any(any(c for c in r) for r in rows):
                        tables.append(Block("table", page=number, table_ref=f"p{number}-t{index}", rows=rows))
                source = page.filter(_outside([t.bbox for t in found])) if found else page
                text = source.extract_text() or ""
                method, confidence = "texte", None

                if len(text.strip()) < settings.ocr_min_chars:
                    if ocr_engine is not None:
                        import pypdfium2

                        if pdfium_doc is None:
                            pdfium_doc = pypdfium2.PdfDocument(str(path))
                        image = pdfium_doc[number - 1].render(scale=settings.ocr_dpi / 72).to_pil()
                        ocr = ocr_engine(image)
                        text, method, confidence = ocr.text, f"ocr:{ocr.engine}", ocr.confidence
                        tables = []  # le tableau éventuel est restitué par l'OCR
                        if confidence is not None and confidence < 0.6:
                            result.warnings.append(
                                f"Page {number} : OCR de faible confiance ({confidence:.0%}), à vérifier."
                            )
                    elif not text.strip():
                        method = "vide"
                        result.warnings.append(f"Page {number} sans texte (OCR désactivé).")

                result.pages.append(PageInfo(number, text, method, confidence))
                result.blocks.append(Block("text", text=text, page=number))
                result.blocks.extend(tables)
    finally:
        if pdfium_doc is not None:
            pdfium_doc.close()
    return result
