"""Lecteurs par format. Chaque lecteur retourne un ParsedDocument."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from ...config import Settings
from ..types import ParsedDocument

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".xlsm"}
LEGACY_EXTENSIONS = {".doc": "docx", ".xls": "xlsx", ".rtf": "docx", ".odt": "docx", ".ods": "xlsx"}


class UnsupportedFormat(ValueError):
    pass


def _convert_legacy(path: Path, target: str, workdir: Path) -> Path:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise UnsupportedFormat(
            f"{path.name} : format {path.suffix} non pris en charge sans LibreOffice "
            "(construire l'image ingest avec INSTALL_LIBREOFFICE=true ou convertir le fichier)."
        )
    subprocess.run(
        [soffice, "--headless", "--convert-to", target, "--outdir", str(workdir), str(path)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    converted = workdir / (path.stem + "." + target)
    if not converted.exists():
        raise UnsupportedFormat(f"Conversion LibreOffice de {path.name} échouée.")
    return converted


def parse_file(path: Path, settings: Settings, ocr_engine=None) -> ParsedDocument:
    ext = path.suffix.lower()
    if ext in LEGACY_EXTENSIONS:
        with tempfile.TemporaryDirectory() as tmp:
            converted = _convert_legacy(path, LEGACY_EXTENSIONS[ext], Path(tmp))
            parsed = parse_file(converted, settings, ocr_engine)
            parsed.warnings.append(f"Converti depuis {ext} avec LibreOffice.")
            return parsed
    if ext == ".pdf":
        from .pdf import parse_pdf

        return parse_pdf(path, settings, ocr_engine)
    if ext == ".docx":
        from .word import parse_docx

        return parse_docx(path)
    if ext in {".xlsx", ".xlsm"}:
        from .excel import parse_xlsx

        return parse_xlsx(path)
    raise UnsupportedFormat(f"{path.name} : extension {ext} non prise en charge.")
