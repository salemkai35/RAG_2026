"""Documents Word (.docx) : paragraphes, titres (styles), tableaux, dans l'ordre."""

from __future__ import annotations

import re
from pathlib import Path

from ..types import Block, ParsedDocument

_HEADING_STYLE = re.compile(r"^(heading|titre)\s*(\d)?", re.IGNORECASE)


def parse_docx(path: Path) -> ParsedDocument:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(str(path))
    result = ParsedDocument()
    table_index = 0
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            if not text:
                continue
            style = (paragraph.style.name if paragraph.style is not None else "") or ""
            match = _HEADING_STYLE.match(style)
            if match:
                level = int(match.group(2)) if match.group(2) else 1
                result.blocks.append(Block("heading", text=text, level=level))
            else:
                result.blocks.append(Block("text", text=text))
        elif tag == "tbl":
            table_index += 1
            table = Table(child, document)
            rows = []
            for row in table.rows:
                cells = []
                previous = None
                for cell in row.cells:
                    # Les cellules fusionnées sont répétées par python-docx : on dédoublonne.
                    if previous is not None and cell._tc is previous:
                        continue
                    cells.append(cell.text.strip())
                    previous = cell._tc
                rows.append(cells)
            result.blocks.append(Block("table", table_ref=f"tableau-{table_index}", rows=rows))
    result.warnings.append("Format Word : pas de numéro de page (traçabilité par section).")
    return result
