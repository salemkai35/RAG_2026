"""
title: Contrats – Recherche et consultation
author: IAP – Département Études & Projets
version: 0.1.0
description: Recherche hybride dans les contrats de travaux et leurs pièces, et consultation des fiches contrats, avec sources (document, page, section, cellule).
"""

# Ce Tool est volontairement minimal : toute la logique est dans le package
# contract_rag installé dans l'image Open WebUI (voir docker/openwebui.Dockerfile).
# Ne pas ajouter de "requirements:" ici (installation pip désactivée hors Internet).

from typing import Optional

from pydantic import BaseModel, Field

from contract_rag.owui import adapters


class Tools:
    class Valves(BaseModel):
        nombre_passages: int = Field(default=8, description="Nombre de passages renvoyés au modèle (1-20).")

    def __init__(self):
        self.valves = self.Valves()
        # Les citations (document, page, section) sont émises par le Tool lui-même.
        self.citation = False

    async def rechercher_dans_contrats(
        self,
        question: str,
        reference_contrat: Optional[str] = None,
        type_document: Optional[str] = None,
        entreprise: Optional[str] = None,
        __user__: Optional[dict] = None,
        __event_emitter__=None,
    ) -> str:
        """
        Recherche les passages pertinents dans les contrats de travaux et leurs documents associés
        (avenants, ordres de service, PV de réception, bordereaux de prix, cautions, cahiers des charges).
        À utiliser pour toute question sur le contenu des contrats. Chaque passage renvoyé est accompagné
        de sa source (fichier, page, section, tableau ou cellule) qu'il faut citer sous la forme [n].
        :param question: La question ou les mots-clés recherchés, en français.
        :param reference_contrat: Référence(s) de contrat pour restreindre la recherche, séparées par des virgules (optionnel).
        :param type_document: Type(s) de document : contrat, avenant, ordre_service, pv_reception_provisoire, pv_reception_definitive, levee_reserves, garantie, bordereau_prix, cahier_charges, annexe (optionnel).
        :param entreprise: Nom (ou partie du nom) de l'entreprise titulaire pour filtrer (optionnel).
        """
        nombre = max(1, min(20, self.valves.nombre_passages))
        return await adapters.rechercher_passages(
            question, reference_contrat, type_document, entreprise, nombre, __user__, __event_emitter__
        )

    async def rechercher_contrats(
        self,
        texte: str = "",
        entreprise: Optional[str] = None,
        __user__: Optional[dict] = None,
        __event_emitter__=None,
    ) -> str:
        """
        Liste les contrats du référentiel correspondant à une référence, un intitulé, un objet ou une entreprise.
        À utiliser pour identifier un contrat ou obtenir la liste des contrats d'un titulaire.
        :param texte: Référence, intitulé ou mots de l'objet du contrat (vide pour tout lister).
        :param entreprise: Nom (ou partie du nom) de l'entreprise titulaire (optionnel).
        """
        return await adapters.rechercher_contrats(texte, entreprise, __user__, __event_emitter__)

    async def fiche_contrat(
        self,
        reference: str,
        __user__: Optional[dict] = None,
        __event_emitter__=None,
    ) -> str:
        """
        Renvoie la fiche d'un contrat : informations du référentiel (montants, dates, délais, titulaire),
        documents rattachés et versions, avenants, évolution du montant calculée, informations extraites
        avec leur source, et contrôles de cohérence / informations manquantes.
        :param reference: Référence exacte du contrat.
        """
        return await adapters.fiche_contrat(reference, __user__, __event_emitter__)
