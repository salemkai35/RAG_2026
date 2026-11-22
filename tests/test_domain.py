from datetime import date
from decimal import Decimal as D

import pytest

from contract_rag.domain.deadlines import ajouter_mois, date_fin_contractuelle, jours_retard
from contract_rag.domain.financial import (
    AvenantFinancier,
    coherence_ht_ttc,
    evolution_montant,
    montant_ttc,
    penalites_retard,
)
from contract_rag.domain.parsing import parse_date, parse_decimal


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("125 000 000,00", D("125000000.00")),
        ("125.000.000,50", D("125000000.50")),
        ("1,234,567.89", D("1234567.89")),
        ("1234567.89", D("1234567.89")),
        ("12 500,75 DA", D("12500.75")),
        ("19 %", D("19")),
        (1500, D("1500")),
        ("", None),
        ("n/a", None),
    ],
)
def test_parse_decimal(raw, expected):
    assert parse_decimal(raw) == expected


def test_parse_date():
    assert parse_date("12/03/2023") == date(2023, 3, 12)
    assert parse_date("2023-03-12") == date(2023, 3, 12)
    assert parse_date("31/02/2023") is None


def test_ttc_and_coherence():
    assert montant_ttc(D("125000000"), D("19")) == D("148750000.00")
    assert coherence_ht_ttc(D("125000000"), D("19"), D("148750000.00")) == (True, D("0.00"))
    ok, ecart = coherence_ht_ttc(D("125000000"), D("19"), D("148000000.00"))
    assert not ok and ecart == D("-750000.00")


def test_evolution_montant_with_delta_and_new_amount():
    lignes = evolution_montant(
        D("1000000"),
        [AvenantFinancier(2, nouveau_montant_ht=D("1250000")), AvenantFinancier(1, montant_ht_delta=D("100000"))],
    )
    assert [l.etape for l in lignes] == ["Contrat initial", "Avenant n°1", "Avenant n°2"]
    assert lignes[1].montant_ht == D("1100000.00")
    assert lignes[2].variation_ht == D("150000.00")
    assert lignes[2].cumul_variation_pct == D("25.00")


def test_evolution_montant_inconsistent_avenant():
    with pytest.raises(ValueError, match="incohérente"):
        evolution_montant(D("1000"), [AvenantFinancier(1, D("100"), D("1200"))])


def test_penalites():
    assert penalites_retard(D("1000000"), 10, D("0.1")) == (D("10000.00"), False)
    assert penalites_retard(D("1000000"), 200, D("0.1"), plafond_pct=D("10")) == (D("100000.00"), True)
    assert penalites_retard(D("1000000"), 0, D("0.1")) == (D("0.00"), False)


def test_delais():
    assert ajouter_mois(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert date_fin_contractuelle(date(2023, 4, 2), delai_jours=240) == date(2023, 11, 28)
    assert date_fin_contractuelle(date(2023, 4, 2), delai_jours=240, jour_debut_inclus=True) == date(2023, 11, 27)
    assert date_fin_contractuelle(date(2023, 4, 2), delai_mois=8, prolongations_jours=[30, 15]) == date(2024, 1, 16)
    assert jours_retard(date(2023, 11, 28), date(2023, 12, 8)) == 10
    assert jours_retard(date(2023, 11, 28), date(2023, 11, 1)) == 0
    with pytest.raises(ValueError):
        date_fin_contractuelle(date(2023, 4, 2))
