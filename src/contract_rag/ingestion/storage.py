"""Stockage des fichiers originaux : immuables, adressés par leur empreinte SHA-256."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def store_original(root: Path, source: Path, sha256: str) -> str:
    """Copie le fichier dans originals/ab/<sha><ext> ; retourne le chemin relatif."""
    relative = Path("originals") / sha256[:2] / f"{sha256}{source.suffix.lower()}"
    target = root / relative
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        shutil.copy2(source, tmp)
        tmp.replace(target)
    return relative.as_posix()
