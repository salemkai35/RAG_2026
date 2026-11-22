"""Consultation des contrats : recherche par référence / libellé / titulaire, fiche détaillée."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..config import Settings, get_settings
from ..domain.financial import AvenantFinancier, coherence_ht_ttc, evolution_montant
from ..repositories import contracts as repo
from ..security.authz import UserContext, resolve_scope


@dataclass
class ContractLookup:
    contrat: dict | None
    candidats: list[dict] = field(default_factory=list)
    acces_refuse: bool = False


class ContractService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def find_contracts(self, conn, text: str, user: UserContext, entreprise: str | None = None,
                       limit: int = 10) -> list[dict]:
        scope = resolve_scope(conn, user, self.settings.default_access)
        if scope.is_empty:
            return []
        return repo.search(conn, text, scope, limit, entreprise)

    def resolve(self, conn, reference: str, user: UserContext) -> ContractLookup:
        """Référence exacte, sinon meilleurs candidats approchants (jamais de choix implicite)."""
        scope = resolve_scope(conn, user, self.settings.default_access)
        contrat = repo.get_by_reference(conn, reference)
        if contrat:
            if not scope.allows(contrat["id"]):
                return ContractLookup(None, acces_refuse=True)
            return ContractLookup(contrat)
        return ContractLookup(None, candidats=repo.search(conn, reference, scope, 5) if not scope.is_empty else [])

    def details(self, conn, contrat_id: int) -> dict:
        data = repo.details(conn, contrat_id)
        data["controles"] = self.controls(data)
        data["evolution_montant"] = self.amount_evolution(data)
        return data

    @staticmethod
    def amount_evolution(data: dict):
        contrat = data["contrat"]
        avenants = [a for a in data["avenants"]
                    if a["montant_ht_delta"] is not None or a["nouveau_montant_ht"] is not None]
        if not contrat or contrat["montant_initial_ht"] is None or not avenants:
            return None
        try:
            return evolution_montant(
                Decimal(contrat["montant_initial_ht"]),
                [AvenantFinancier(a["numero"], a["montant_ht_delta"], a["nouveau_montant_ht"]) for a in avenants],
            )
        except ValueError as exc:
            return str(exc)

    @staticmethod
    def controls(data: dict) -> list[str]:
        """Contrôles déterministes de cohérence et de complétude."""
        alerts = []
        contrat = data["contrat"]
        if not contrat:
            return alerts
        ht, tva, ttc = contrat["montant_initial_ht"], contrat["taux_tva"], contrat["montant_initial_ttc"]
        if ht is not None and tva is not None and ttc is not None:
            ok, ecart = coherence_ht_ttc(Decimal(ht), Decimal(tva), Decimal(ttc))
            if not ok:
                alerts.append(f"Incohérence HT/TVA/TTC : écart de {ecart} sur le montant initial.")
        for label, key in (("montant initial HT", "montant_initial_ht"), ("date de signature", "date_signature"),
                           ("délai d'exécution", "delai_execution_jours")):
            if contrat.get(key) is None and not (key == "delai_execution_jours" and contrat.get("delai_execution_mois")):
                alerts.append(f"Information non renseignée dans le référentiel : {label}.")
        types = {d["type_document"] for d in data["documents"]}
        if "contrat" not in types:
            alerts.append("Aucun document de type « contrat » rattaché.")
        numeros_docs = sorted({d["numero_ordre"] for d in data["documents"]
                               if d["type_document"] == "avenant" and d["numero_ordre"]})
        if numeros_docs:
            manquants = sorted(set(range(1, max(numeros_docs) + 1)) - set(numeros_docs))
            if manquants:
                alerts.append("Avenant(s) absent(s) du corpus : n° " + ", ".join(map(str, manquants)) + ".")
        erreurs = [d["nom_fichier"] for d in data["documents"] if d["statut_traitement"] == "erreur"]
        if erreurs:
            alerts.append("Documents en erreur d'extraction : " + ", ".join(erreurs) + ".")
        return alerts
