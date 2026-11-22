"""Déduction du type de document à partir de son nom de fichier.

Heuristique volontairement simple et prévisible ; un ``manifest.csv`` permet
toujours de forcer le type (voir inbox/README.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.parsing import normalize_text

DOCUMENT_TYPES = (
    "contrat",
    "avenant",
    "ordre_service",
    "pv_reception_provisoire",
    "pv_reception_definitive",
    "levee_reserves",
    "mainlevee_garantie",
    "garantie",
    "bordereau_prix",
    "cahier_charges",
    "annexe",
    "autre",
)


@dataclass(frozen=True)
class Classification:
    type_document: str
    numero_ordre: int | None = None


# (type, motif de détection, motif du numéro d'ordre) – l'ordre compte.
_RULES: list[tuple[str, re.Pattern, re.Pattern | None]] = [
    ("avenant", re.compile(r"\bavenant"), re.compile(r"avenant\D{0,8}?(\d{1,3})\b")),
    (
        "ordre_service",
        re.compile(r"\b(ods?|o\s?s|ordres?\s+de\s+service)\b"),
        re.compile(r"(?:\bods?|\bo\s?s|service)\D{0,8}?(\d{1,3})\b"),
    ),
    ("pv_reception_definitive", re.compile(r"(reception\s+definitive|\bpvrd\b)"), None),
    ("pv_reception_provisoire", re.compile(r"(reception\s+provisoire|\bpvrp\b)"), None),
    ("levee_reserves", re.compile(r"lev(ee)?\s+(des\s+)?reserves"), None),
    ("mainlevee_garantie", re.compile(r"main\s?levee"), None),
    ("garantie", re.compile(r"(caution|cautionnement|garantie)"), None),
    ("bordereau_prix", re.compile(r"(\bbpu\b|\bdqe\b|\bdpgf\b|bordereau|devis\s+quantitatif)"), None),
    ("cahier_charges", re.compile(r"(\bcps\b|\bccap\b|\bcctp\b|\bccag\b|cahier\s+des\s+(charges|prescriptions|clauses))"), None),
    ("contrat", re.compile(r"(\bcontrat\b|\bmarche\b|\bconvention\b)"), None),
    ("annexe", re.compile(r"\bannexe"), None),
]


def _prepare(filename: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", filename)
    text = normalize_text(stem)
    text = text.replace("n°", " ").replace("no.", " ")
    return re.sub(r"[_\-.()\[\]]+", " ", text)


def classify_filename(filename: str) -> Classification:
    text = _prepare(filename)
    for doc_type, pattern, number_pattern in _RULES:
        if pattern.search(text):
            numero = None
            if number_pattern:
                match = number_pattern.search(text)
                if match:
                    numero = int(match.group(1))
            return Classification(doc_type, numero)
    return Classification("autre", None)
