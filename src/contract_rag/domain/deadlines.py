"""Calculs de délais contractuels (jours calendaires)."""

from __future__ import annotations

import calendar
from datetime import date, timedelta


def ajouter_mois(depart: date, mois: int) -> date:
    """Ajoute des mois calendaires (31/01 + 1 mois -> 28 ou 29/02)."""
    total = depart.month - 1 + mois
    annee, mois_index = depart.year + total // 12, total % 12 + 1
    jour = min(depart.day, calendar.monthrange(annee, mois_index)[1])
    return date(annee, mois_index, jour)


def date_fin_contractuelle(
    date_debut: date,
    delai_jours: int | None = None,
    delai_mois: int | None = None,
    prolongations_jours: list[int] | None = None,
    jour_debut_inclus: bool = False,
) -> date:
    """Date d'achèvement contractuelle.

    ``date_debut`` est généralement la date d'effet de l'ODS de démarrage.
    ``jour_debut_inclus`` : True si le jour de démarrage compte comme 1er jour
    (fin = début + délai - 1). La convention applicable doit être lue dans le contrat.
    """
    if delai_jours is None and delai_mois is None:
        raise ValueError("Un délai en jours ou en mois est requis.")
    fin = date_debut
    if delai_mois:
        fin = ajouter_mois(fin, delai_mois)
    if delai_jours:
        fin = fin + timedelta(days=delai_jours)
    fin = fin + timedelta(days=sum(prolongations_jours or []))
    if jour_debut_inclus:
        fin = fin - timedelta(days=1)
    return fin


def jours_retard(date_fin_prevue: date, date_achevement: date) -> int:
    """Nombre de jours calendaires de retard (0 si en avance ou à l'heure)."""
    return max(0, (date_achevement - date_fin_prevue).days)
