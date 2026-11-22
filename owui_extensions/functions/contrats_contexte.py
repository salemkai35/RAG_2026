"""
title: Contrats – Recherche systématique (RAG forcé)
author: IAP – Département Études & Projets
version: 0.1.0
description: Avant chaque réponse, recherche les passages pertinents dans la base contractuelle et les injecte avec leurs sources. Fiable même si le LLM ne sait pas appeler les outils.
"""

# Function de type Filter : à associer au modèle « Assistant Contrats »
# (Espace de travail > Modèles > Filtres) ou à activer globalement par l'administrateur.
# Toute la logique est dans le package contract_rag (image Open WebUI dérivée).

from typing import Optional

from pydantic import BaseModel, Field

from contract_rag.owui import adapters


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=0, description="Ordre d'exécution parmi les filtres.")
        nombre_passages: int = Field(default=6, description="Nombre de passages injectés (1-15).")
        active: bool = Field(default=True, description="Désactiver pour revenir aux seuls Tools.")
        controle_sortie: bool = Field(
            default=True, description="Signaler les montants de la réponse absents des sources.")

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__=None,
    ) -> dict:
        if not self.valves.active:
            return body
        # Les tâches internes d'Open WebUI (titre, tags, suggestions) ne déclenchent pas de recherche.
        if (__metadata__ or {}).get("task"):
            return body
        messages = body.get("messages") or []
        question = adapters.derniere_question(messages)
        if not question:
            return body
        nombre = max(1, min(15, self.valves.nombre_passages))
        cle = adapters.cle_conversation(body, __metadata__)
        contexte = await adapters.contexte_force(question, __user__, __event_emitter__, nombre, cle)
        # Le contexte est inséré juste avant la dernière question de l'utilisateur.
        index = max(i for i, m in enumerate(messages) if m.get("role") == "user")
        messages.insert(index, {"role": "system", "content": contexte})
        body["messages"] = messages
        return body

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__=None,
    ) -> dict:
        """Garde-fou : signale les montants / dates de la réponse absents des sources injectées."""
        if not self.valves.active or not self.valves.controle_sortie:
            return body
        messages = body.get("messages") or []
        if not messages or messages[-1].get("role") != "assistant":
            return body
        alerte = adapters.controle_reponse(messages[-1].get("content") or "",
                                           adapters.cle_conversation(body, __metadata__))
        if alerte:
            messages[-1]["content"] = (messages[-1].get("content") or "") + alerte
        return body
