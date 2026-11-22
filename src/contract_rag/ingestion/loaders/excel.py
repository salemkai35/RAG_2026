"""Classeurs Excel : chaque feuille devient des tableaux avec références de cellules."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from ..types import Block, ParsedDocument

ROWS_PER_BLOCK = 40


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat(" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return format(Decimal(repr(value)).normalize(), "f")
    return str(value).strip()


def parse_xlsx(path: Path) -> ParsedDocument:
    import openpyxl
    from openpyxl.utils import get_column_letter

    result = ParsedDocument()
    workbook = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    try:
        for sheet in workbook.worksheets:
            # iter_rows démarre en A1 : le numéro de ligne Excel = index + 1.
            rows: list[tuple[int, list[str]]] = []
            for index, values in enumerate(sheet.iter_rows(min_row=1, min_col=1, values_only=True), start=1):
                cells = [_fmt(v) for v in values]
                if any(cells):
                    rows.append((index, cells))
            if not rows:
                continue
            # Colonnes réellement utilisées
            used = [i for _, cells in rows for i, c in enumerate(cells) if c]
            first_col, last_col = min(used), max(used)
            rows = [(n, cells[first_col : last_col + 1]) for n, cells in rows]
            col_a, col_b = get_column_letter(first_col + 1), get_column_letter(last_col + 1)

            header_row, header = rows[0]
            body = rows[1:]
            parts = [body[i : i + ROWS_PER_BLOCK] for i in range(0, len(body), ROWS_PER_BLOCK)] or [[]]
            for part_index, part in enumerate(parts, start=1):
                r_first = part[0][0] if part else header_row
                r_last = part[-1][0] if part else header_row
                result.blocks.append(
                    Block(
                        "table",
                        table_ref=f"{sheet.title}#{part_index}",
                        cell_ref=f"'{sheet.title}'!{col_a}{r_first}:{col_b}{r_last}",
                        rows=[header] + [cells for _, cells in part],
                    )
                )
    finally:
        workbook.close()
    result.warnings.append(
        "Excel : valeurs des formules telles qu'enregistrées lors de la dernière sauvegarde du fichier."
    )
    return result
