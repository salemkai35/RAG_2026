"""Pont entre les Tools Open WebUI et les services métier.

* convertit ``__user__`` en UserContext (droits métier) ;
* exécute les services synchrones hors de la boucle asyncio (``asyncio.to_thread``)
  pour ne jamais bloquer Open WebUI ;
* émet les statuts et les citations via ``__event_emitter__`` ;
* transforme toute erreur technique en message explicite pour le modèle.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections import OrderedDict
import logging
from typing import Any, Awaitable, Callable

from .. import presentation
from ..db import connection
from ..ingestion.classify import DOCUMENT_TYPES
from ..domain.parsing import normalize_text
from ..retrieval.hybrid import SearchFilters
from ..security.authz import UserContext
from ..services.contract_service import ContractService
from ..services.search_service import SearchService

log = logging.getLogger("contract_rag.owui")

Emitter = Callable[[dict], Awaitable[Any]] | None

_search_service: SearchService | None = None
_contract_service: ContractService | None = None


def _services() -> tuple[SearchService, ContractService]:
    global _search_service, _contract_service
    if _search_service is None:
        _search_service = SearchService()
    if _contract_service is None:
        _contract_service = ContractService()
    return _search_service, _contract_service


async def _lookup_groups(user_id: str) -> tuple[str, ...]:
    """Groupes Open WebUI de l'utilisateur (lecture seule).

    L'API interne est asynchrone en v0.13 (synchrone dans des versions antérieures) :
    les deux cas sont gérés ; toute autre évolution de l'API donne « aucun groupe ».
    """
    try:
        from open_webui.models.groups import Groups  # type: ignore

        result = Groups.get_groups_by_member_id(user_id)
        if inspect.isawaitable(result):
            result = await result
        return tuple(g.name for g in (result or []))
    except Exception:  # module absent ou API modifiée : pas de groupes
        return ()


def user_context(owui_user: dict | None, groups: tuple[str, ...] | None = None) -> UserContext:
    u = owui_user or {}
    if groups is None:
        raw_groups = u.get("groups") or []
        groups = tuple(g.get("name") if isinstance(g, dict) else str(g) for g in raw_groups)
    return UserContext(
        id=str(u.get("id", "")),
        email=(u.get("email") or "").lower(),
        name=u.get("name") or "",
        role=u.get("role") or "user",
        groups=tuple(g for g in groups if g),
    )


async def resolve_user(owui_user: dict | None) -> UserContext:
    """UserContext complet : groupes de ``__user__`` ou, à défaut, lus dans Open WebUI."""
    ctx = user_context(owui_user)
    if not ctx.groups and ctx.id:
        ctx = user_context(owui_user, await _lookup_groups(ctx.id))
    return ctx


async def _emit(emitter: Emitter, event: dict) -> None:
    if emitter is None:
        return
    try:
        await emitter(event)
    except Exception as exc:  # l'affichage ne doit jamais faire échouer le Tool
        log.debug("Émission d'événement impossible : %s", exc)


async def _status(emitter: Emitter, description: str, done: bool = False) -> None:
    await _emit(emitter, {"type": "status", "data": {"description": description, "done": done}})


def _types(value: str | None) -> list[str] | None:
    if not value:
        return None
    result = []
    for raw in value.split(","):
        key = normalize_text(raw).replace(" ", "_").replace("'", "_")
        key = {"os": "ordre_service", "ods": "ordre_service", "bpu": "bordereau_prix",
               "caution": "garantie", "pv": "pv_reception_provisoire"}.get(key, key)
        matches = [t for t in DOCUMENT_TYPES if t == key or t.startswith(key)]
        result += matches[:1]
    return result or None


async def _run(emitter: Emitter, label: str, func: Callable[[], str]) -> str:
    await _status(emitter, label)
    try:
        output = await asyncio.to_thread(func)
        await _status(emitter, label + " – terminé", done=True)
        return output
    except Exception as exc:
        log.exception("Erreur dans le Tool contrats")
        await _status(emitter, "Erreur technique", done=True)
        return (
            "ERREUR TECHNIQUE lors de l'accès à la base contractuelle : "
            f"{type(exc).__name__}: {exc}. Indique à l'utilisateur que l'information n'a pas pu être "
            "consultée ; ne réponds pas de mémoire."
        )


# ---------------------------------------------------------------------------
# Fonctions appelées par les Tools
# ---------------------------------------------------------------------------

async def rechercher_passages(question: str, reference_contrat: str | None, type_document: str | None,
                              entreprise: str | None, nombre: int, owui_user: dict | None,
                              emitter: Emitter) -> str:
    user = await resolve_user(owui_user)
    search, _ = _services()
    filters = SearchFilters(
        contrat_refs=[r.strip() for r in reference_contrat.split(",")] if reference_contrat else None,
        types_document=_types(type_document),
        entreprise=entreprise or None,
    )
    citations: list[dict] = []

    def work() -> str:
        with connection() as conn:
            result = search.search_passages(conn, question, user, filters, top_k=nombre)
        citations.extend(presentation.citation_events(result.passages))
        return presentation.passages_markdown(result.passages, result.warnings)

    output = await _run(emitter, "Recherche dans les contrats…", work)
    for event in citations:
        await _emit(emitter, event)
    return output


CONTEXT_HEADER = (
    "CONTEXTE DOCUMENTAIRE (recherche automatique dans la base contractuelle) :\n"
    "Réponds UNIQUEMENT à partir des passages ci-dessous et cite-les sous la forme [n]. "
    "Si l'information demandée n'y figure pas, réponds exactement : "
    "« Information non trouvée dans les documents indexés. » N'invente aucun chiffre.\n\n"
)


# Contexte injecté par conversation (mémoire du processus, bornée) pour le contrôle en sortie.
_CONTEXTES: "OrderedDict[str, str]" = OrderedDict()
_CONTEXTES_MAX = 500


def cle_conversation(body: dict | None, metadata: dict | None) -> str | None:
    for source in (metadata or {}, body or {}):
        value = source.get("chat_id") or source.get("session_id")
        if value:
            return str(value)
    return None


async def contexte_force(question: str, owui_user: dict | None, emitter: Emitter, nombre: int,
                         cle: str | None = None) -> str:
    """Recherche systématique utilisée par le Filter : ne dépend pas de l'appel d'outils du LLM."""
    passages = await rechercher_passages(question, None, None, None, nombre, owui_user, emitter)
    if cle:
        _CONTEXTES[cle] = question + "\n" + passages
        _CONTEXTES.move_to_end(cle)
        while len(_CONTEXTES) > _CONTEXTES_MAX:
            _CONTEXTES.popitem(last=False)
    return CONTEXT_HEADER + passages


def controle_reponse(reponse: str, cle: str | None) -> str | None:
    """Avertissement si la réponse contient des valeurs absentes des sources injectées."""
    contexte = _CONTEXTES.get(cle or "")
    if not contexte or not reponse:
        return None
    absents = presentation.montants_non_sources(reponse, contexte)
    if not absents:
        return None
    return ("\n\n> ⚠️ **Contrôle automatique** : les valeurs suivantes ne figurent pas dans les "
            "passages sources et peuvent avoir été calculées ou inventées par le modèle — "
            "à vérifier : " + " ; ".join(absents[:10]))


def derniere_question(messages: list[dict]) -> str | None:
    """Texte du dernier message utilisateur (contenu simple ou multimodal)."""
    for message in reversed(messages or []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        return (content or "").strip() or None
    return None


async def rechercher_contrats(texte: str, entreprise: str | None, owui_user: dict | None,
                              emitter: Emitter) -> str:
    user = await resolve_user(owui_user)
    _, contracts = _services()

    def work() -> str:
        with connection() as conn:
            rows = contracts.find_contracts(conn, texte or "", user, entreprise=entreprise)
        return presentation.contracts_markdown(rows)

    return await _run(emitter, "Recherche de contrats…", work)


async def fiche_contrat(reference: str, owui_user: dict | None, emitter: Emitter) -> str:
    user = await resolve_user(owui_user)
    _, contracts = _services()

    def work() -> str:
        with connection() as conn:
            lookup = contracts.resolve(conn, reference, user)
            if lookup.acces_refuse:
                return f"Accès refusé : l'utilisateur n'est pas autorisé à consulter le contrat {reference}."
            if lookup.contrat is None:
                if lookup.candidats:
                    return (f"Aucun contrat de référence exacte « {reference} ». Contrats approchants "
                            "(demander à l'utilisateur de préciser) :\n\n"
                            + presentation.contracts_markdown(lookup.candidats))
                return f"Aucun contrat « {reference} » dans le référentiel."
            return presentation.contract_details_markdown(contracts.details(conn, lookup.contrat["id"]))

    return await _run(emitter, f"Consultation du contrat {reference}…", work)
