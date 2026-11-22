"""Mise en forme des résultats pour le LLM (Markdown) et des citations Open WebUI.

Les Tools renvoient des textes déjà structurés et sourcés : le LLM n'a pas à
recalculer ni à reformuler les chiffres.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from .services.search_service import Passage

TYPE_LABELS = {
    "contrat": "Contrat", "avenant": "Avenant", "ordre_service": "Ordre de service",
    "pv_reception_provisoire": "PV de réception provisoire",
    "pv_reception_definitive": "PV de réception définitive", "levee_reserves": "Levée des réserves",
    "mainlevee_garantie": "Mainlevée de garantie", "garantie": "Caution / garantie",
    "bordereau_prix": "Bordereau des prix", "cahier_charges": "Cahier des charges",
    "annexe": "Annexe", "autre": "Autre",
}

NON_TROUVE = "Information non trouvée dans les documents indexés."


def fmt_montant(value, devise: str | None = None) -> str:
    if value is None:
        return "non renseigné"
    q = Decimal(value).quantize(Decimal("0.01"))
    entier, _, dec = f"{q:,.2f}".partition(".")
    text = entier.replace(",", " ") + "," + dec
    return f"{text} {devise}" if devise else text


def fmt_pct(value) -> str:
    """Decimal('19.000') -> '19' ; Decimal('7.500') -> '7,5'."""
    if value is None:
        return "non renseigné"
    text = format(Decimal(value).normalize(), "f")
    return text.replace(".", ",")


def fmt_date(value) -> str:
    if value is None:
        return "non renseignée"
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return str(value)


def type_label(type_document: str, numero: int | None = None) -> str:
    label = TYPE_LABELS.get(type_document, type_document)
    return f"{label} n°{numero}" if numero else label


_NUMBER = re.compile(r"\d{1,3}(?:[   .,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")


def _canonical_numbers(text: str, min_digits: int = 4) -> dict[str, str]:
    """{valeur canonique: forme écrite} des nombres d'au moins ``min_digits`` chiffres."""
    from .domain.parsing import parse_decimal

    found: dict[str, str] = {}
    for token in _NUMBER.findall(text or ""):
        value = parse_decimal(token)
        if value is None:
            continue
        canonical = format(value.normalize(), "f")
        if sum(c.isdigit() for c in canonical.split(".")[0]) >= min_digits:
            found.setdefault(canonical, token.strip())
    return found


def montants_non_sources(reponse: str, sources: str) -> list[str]:
    """Nombres significatifs (montants, dates, quantités) de la réponse absents des sources.

    Garde-fou de traçabilité : signale notamment les montants recalculés par le LLM.
    """
    connus = _canonical_numbers(sources, min_digits=1)
    return [ecrit for canon, ecrit in _canonical_numbers(reponse).items() if canon not in connus]


def passages_markdown(passages: list[Passage], warnings: list[str] | None = None) -> str:
    if not passages:
        lines = [NON_TROUVE]
        lines += [f"> ⚠️ {w}" for w in warnings or []]
        return "\n".join(lines)
    lines = [
        "Passages trouvés (citer les sources sous la forme [n] ; ne rien affirmer qui n'y figure pas) :",
        "",
    ]
    for i, p in enumerate(passages, start=1):
        ref = p.contrat_reference or "sans contrat"
        lines.append(f"[{i}] Contrat {ref} — {type_label(p.type_document, p.numero_ordre)} — {p.source_label}")
        if p.section_chemin:
            lines.append(f"Section : {p.section_chemin}")
        lines.append(p.contenu.strip())
        lines.append("")
    lines += [f"> ⚠️ {w}" for w in warnings or []]
    return "\n".join(lines).strip()


def citation_events(passages: list[Passage]) -> list[dict]:
    """Événements 'citation' pour l'interface Open WebUI (format à valider sur la version épinglée)."""
    events = []
    for i, p in enumerate(passages, start=1):
        label = f"[{i}] {p.contrat_reference or ''} – {p.source_label}".strip()
        metadata = {
            "source": label,
            "name": label,
            "document_id": p.document_id,
            "chunk_id": p.chunk_id,
            "page": p.page_debut,
            "section": p.section_chemin,
            "cellule": p.cellule_ref,
        }
        events.append({
            "type": "citation",
            "data": {
                "document": [p.contenu],
                "metadata": [{k: v for k, v in metadata.items() if v is not None}],
                "source": {"name": label},
            },
        })
    return events


def contracts_markdown(rows: list[dict]) -> str:
    if not rows:
        return "Aucun contrat correspondant n'a été trouvé (ou aucun contrat n'est accessible)."
    lines = ["| Référence | Intitulé | Titulaire | Montant initial HT | Signature | Docs |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['reference']} | {(r.get('intitule') or r.get('objet') or '—')[:90]} | "
            f"{r.get('titulaire') or '—'} | {fmt_montant(r.get('montant_initial_ht'), r.get('devise'))} | "
            f"{fmt_date(r.get('date_signature'))} | {r.get('nb_documents', 0)} |"
        )
    return "\n".join(lines)


def contract_details_markdown(data: dict) -> str:
    c = data["contrat"]
    devise = c.get("devise")
    delai = (f"{c['delai_execution_jours']} jours" if c.get("delai_execution_jours")
             else f"{c['delai_execution_mois']} mois" if c.get("delai_execution_mois") else "non renseigné")
    lines = [
        f"## Contrat {c['reference']}",
        f"Source des métadonnées : {c.get('source_metadonnees')} "
        "(« ingestion » = non encore extraites ni validées).",
        "",
        f"- Intitulé : {c.get('intitule') or 'non renseigné'}",
        f"- Objet : {c.get('objet') or 'non renseigné'}",
        f"- Maître d'ouvrage : {c.get('maitre_ouvrage') or 'non renseigné'}",
        f"- Titulaire : {c.get('titulaire') or 'non renseigné'}",
        f"- Montant initial HT : {fmt_montant(c.get('montant_initial_ht'), devise)}",
        f"- TVA : {fmt_pct(c['taux_tva'])} %" if c.get("taux_tva") is not None else "- TVA : non renseignée",
        f"- Montant initial TTC : {fmt_montant(c.get('montant_initial_ttc'), devise)}",
        f"- Date de signature : {fmt_date(c.get('date_signature'))}",
        f"- Date de notification : {fmt_date(c.get('date_notification'))}",
        f"- ODS de démarrage : {fmt_date(c.get('date_ods_demarrage'))}",
        f"- Délai d'exécution : {delai}",
    ]
    if data["parties"]:
        lines += ["", "### Parties"] + [f"- {p['role']} : {p['nom']}" for p in data["parties"]]

    lines += ["", "### Documents rattachés"]
    if data["documents"]:
        lines += ["| Type | Fichier | Version | Pages | Statut |", "|---|---|---|---|---|"]
        for d in data["documents"]:
            version = f"v{d['version']}" + ("" if d["est_courant"] else " (remplacée)")
            lines.append(f"| {type_label(d['type_document'], d['numero_ordre'])} | {d['nom_fichier']} | "
                         f"{version} | {d['nb_pages'] or '—'} | {d['statut_traitement']} |")
    else:
        lines.append("Aucun document.")

    if data["avenants"]:
        lines += ["", "### Avenants (référentiel)", "| N° | Signature | Objet | Variation HT | Délai | Statut |",
                  "|---|---|---|---|---|---|"]
        for a in data["avenants"]:
            delta = f"{a['delai_delta_jours']:+d} j" if a["delai_delta_jours"] is not None else "—"
            lines.append(f"| {a['numero']} | {fmt_date(a['date_signature'])} | {(a['objet'] or '—')[:60]} | "
                         f"{fmt_montant(a['montant_ht_delta'], devise)} | {delta} | {a['statut_validation']} |")

    evolution = data.get("evolution_montant")
    if isinstance(evolution, list):
        lines += ["", "### Évolution du montant HT (calcul déterministe)",
                  "| Étape | Variation | Montant HT | Cumul | Cumul % |", "|---|---|---|---|---|"]
        for e in evolution:
            pct = f"{e.cumul_variation_pct} %" if e.cumul_variation_pct is not None else "—"
            lines.append(f"| {e.etape} | {fmt_montant(e.variation_ht)} | {fmt_montant(e.montant_ht, devise)} | "
                         f"{fmt_montant(e.cumul_variation_ht)} | {pct} |")
    elif isinstance(evolution, str):
        lines += ["", f"⚠️ Évolution du montant non calculable : {evolution}"]

    if data["champs"]:
        lines += ["", "### Informations extraites (avec source)"]
        for f in data["champs"]:
            valeur = f["valeur_texte"] or (fmt_montant(f["valeur_numerique"]) if f["valeur_numerique"] is not None
                                           else fmt_date(f["valeur_date"]))
            src = ", ".join(x for x in (f["nom_fichier"], f"p. {f['page']}" if f["page"] else None,
                                        f["section"], f["cellule_ref"]) if x)
            lines.append(f"- {f['champ']} : {valeur} {f['unite'] or ''} — {f['statut']} — source : {src or '—'}")
            if f["extrait"]:
                lines.append(f"  > « {f['extrait'][:300]} »")

    if data.get("controles"):
        lines += ["", "### Contrôles et points d'attention"] + [f"- ⚠️ {a}" for a in data["controles"]]
    return "\n".join(lines)
