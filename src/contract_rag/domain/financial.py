"""Calculs financiers contractuels (Decimal, arrondi au centime)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def q2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def montant_ttc(montant_ht: Decimal, taux_tva_pct: Decimal) -> Decimal:
    """TTC = HT × (1 + TVA/100)."""
    return q2(montant_ht * (Decimal(1) + taux_tva_pct / Decimal(100)))


def coherence_ht_ttc(
    montant_ht: Decimal, taux_tva_pct: Decimal, montant_ttc_annonce: Decimal, tolerance: Decimal = Decimal("1.00")
) -> tuple[bool, Decimal]:
    """Vérifie HT/TVA/TTC ; retourne (cohérent, écart)."""
    ecart = q2(montant_ttc_annonce - montant_ttc(montant_ht, taux_tva_pct))
    return abs(ecart) <= tolerance, ecart


@dataclass(frozen=True)
class AvenantFinancier:
    numero: int
    montant_ht_delta: Decimal | None = None
    nouveau_montant_ht: Decimal | None = None


@dataclass(frozen=True)
class LigneEvolution:
    etape: str
    variation_ht: Decimal
    montant_ht: Decimal
    cumul_variation_ht: Decimal
    cumul_variation_pct: Decimal | None


def evolution_montant(montant_initial_ht: Decimal, avenants: list[AvenantFinancier]) -> list[LigneEvolution]:
    """Évolution du montant HT au fil des avenants (triés par numéro).

    Chaque avenant fournit soit la variation, soit le nouveau montant ; si les
    deux sont fournis, ils doivent être cohérents (sinon ValueError).
    """
    lignes = [
        LigneEvolution("Contrat initial", Decimal(0), q2(montant_initial_ht), Decimal(0),
                       Decimal(0) if montant_initial_ht else None)
    ]
    courant = montant_initial_ht
    for av in sorted(avenants, key=lambda a: a.numero):
        if av.montant_ht_delta is None and av.nouveau_montant_ht is None:
            raise ValueError(f"Avenant n°{av.numero} : ni variation ni nouveau montant renseignés.")
        if av.nouveau_montant_ht is not None:
            delta = av.nouveau_montant_ht - courant
            if av.montant_ht_delta is not None and q2(delta) != q2(av.montant_ht_delta):
                raise ValueError(
                    f"Avenant n°{av.numero} : variation annoncée {q2(av.montant_ht_delta)} "
                    f"incohérente avec le nouveau montant ({q2(delta)})."
                )
            courant = av.nouveau_montant_ht
        else:
            delta = av.montant_ht_delta  # type: ignore[assignment]
            courant = courant + delta
        cumul = courant - montant_initial_ht
        pct = q2(cumul / montant_initial_ht * 100) if montant_initial_ht else None
        lignes.append(LigneEvolution(f"Avenant n°{av.numero}", q2(delta), q2(courant), q2(cumul), pct))
    return lignes


def penalites_retard(
    montant_base: Decimal,
    jours_retard: int,
    taux_journalier_pct: Decimal,
    plafond_pct: Decimal | None = None,
) -> tuple[Decimal, bool]:
    """Pénalités = base × taux journalier × jours, plafonnées le cas échéant.

    Les paramètres (base, taux, plafond) DOIVENT provenir des clauses du contrat :
    aucun taux réglementaire n'est supposé ici. Retourne (montant, plafond_atteint).
    """
    if jours_retard <= 0:
        return Decimal("0.00"), False
    brut = montant_base * taux_journalier_pct / Decimal(100) * jours_retard
    if plafond_pct is not None:
        plafond = montant_base * plafond_pct / Decimal(100)
        if brut > plafond:
            return q2(plafond), True
    return q2(brut), False
