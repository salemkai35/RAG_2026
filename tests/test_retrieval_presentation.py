import ast
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from contract_rag import presentation
from contract_rag.db import migrate
from contract_rag.owui.adapters import _types, user_context
from contract_rag.owui.deploy import parse_frontmatter
from contract_rag.retrieval.hybrid import SearchFilters, _where, build_tsquery
from contract_rag.security.authz import AccessScope, UserContext
from contract_rag.services.contract_service import ContractService
from contract_rag.services.search_service import Passage

ROOT = Path(__file__).resolve().parents[1]


def _passage(**kw):
    base = dict(chunk_id=1, contenu="Pénalité de 1/1000 par jour.", score=0.03, rerank_score=0.9,
                document_id=7, nom_fichier="Contrat.pdf", type_document="avenant", numero_ordre=2,
                version=1, page_debut=12, page_fin=13, section_chemin="Article 12 : Pénalités",
                table_ref=None, cellule_ref=None, contrat_reference="REF-1", contrat_intitule="X")
    base.update(kw)
    return Passage(**base)


def test_tsquery_is_or_of_normalized_words():
    assert build_tsquery("Quel est le délai de l'avenant n°2 ?") == "quel | est | le | delai | de | avenant | 2"
    assert build_tsquery("?? !") is None


def test_where_applies_scope_and_filters():
    params = {}
    sql = _where(SearchFilters(contrat_refs=["ref-1"], types_document=["avenant"], entreprise="Société Éco"),
                 AccessScope(False, frozenset({3, 5})), params)
    assert "d.est_courant" in sql and "scope_ids" in sql and "refs" in sql
    assert params["refs"] == ["REF-1"] and sorted(params["scope_ids"]) == [3, 5]
    assert params["ent"] == "%societe eco%"
    params = {}
    sql = _where(SearchFilters(inclure_versions_remplacees=True), AccessScope(True), params)
    assert "est_courant" not in sql and "scope_ids" not in params


def test_passages_markdown_and_citations():
    p = _passage()
    md = presentation.passages_markdown([p])
    assert "[1] Contrat REF-1 — Avenant n°2 — Contrat.pdf, p. 12-13" in md
    assert "Section : Article 12 : Pénalités" in md
    assert presentation.passages_markdown([], ["Reranker indisponible"]).startswith(presentation.NON_TROUVE)
    event = presentation.citation_events([p])[0]
    assert event["type"] == "citation"
    assert event["data"]["metadata"][0]["page"] == 12
    assert _passage(cellule_ref="'BPU'!B4:D43", page_debut=None).source_label == "Contrat.pdf, 'BPU'!B4:D43"


def test_amount_formatting():
    assert presentation.fmt_montant(D("148750000")) == "148 750 000,00"
    assert presentation.fmt_montant(None) == "non renseigné"
    assert presentation.fmt_date(date(2023, 3, 12)) == "12/03/2023"
    assert presentation.fmt_pct(D("19.000")) == "19"
    assert presentation.fmt_pct(D("7.500")) == "7,5"


def test_contract_controls_detect_gaps():
    data = {
        "contrat": {"montant_initial_ht": D("100"), "taux_tva": D("19"), "montant_initial_ttc": D("150"),
                    "date_signature": None, "delai_execution_jours": None, "delai_execution_mois": 6},
        "documents": [
            {"type_document": "avenant", "numero_ordre": 1, "nom_fichier": "a1.pdf", "statut_traitement": "indexe"},
            {"type_document": "avenant", "numero_ordre": 3, "nom_fichier": "a3.pdf", "statut_traitement": "erreur"},
        ],
        "avenants": [],
    }
    alerts = ContractService.controls(data)
    assert any("HT/TVA/TTC" in a for a in alerts)
    assert any("date de signature" in a for a in alerts)
    assert not any("délai" in a for a in alerts)
    assert any("n° 2" in a for a in alerts)
    assert any("« contrat »" in a for a in alerts)
    assert any("a3.pdf" in a for a in alerts)


def test_authz_principals():
    user = UserContext(id="1", email="X@IAP.dz", groups=("Etudes",))
    assert user.principals == ["*", "user:x@iap.dz", "group:Etudes"]
    ctx = user_context({"id": "u1", "email": "A@B.dz", "role": "user", "groups": [{"name": "G1"}]})
    assert ctx.email == "a@b.dz" and ctx.groups == ("G1",)
    assert AccessScope(False, frozenset({1})).allows(1) and not AccessScope(False).allows(1)


def test_document_type_mapping():
    assert _types("avenant, OS, BPU") == ["avenant", "ordre_service", "bordereau_prix"]
    assert _types("pv_reception_definitive") == ["pv_reception_definitive"]
    assert _types(None) is None


def test_migrations_render_dimension(monkeypatch):
    monkeypatch.setenv("CRAG_EMBEDDING_DIM", "768")
    names = [v for v, _ in migrate.available_migrations()]
    assert names[0] == "001_schema_initial"
    sql = migrate.render(migrate.available_migrations()[0][1])
    assert "vector(768)" in sql and "{{" not in sql


def test_last_user_question_and_filter_file():
    from contract_rag.owui.adapters import derniere_question

    msgs = [{"role": "system", "content": "x"}, {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "R1"},
            {"role": "user", "content": [{"type": "text", "text": "Pénalités ?"}, {"type": "image_url"}]}]
    assert derniere_question(msgs) == "Pénalités ?"
    assert derniere_question([{"role": "assistant", "content": "x"}]) is None
    code = (ROOT / "owui_extensions/functions/contrats_contexte.py").read_text(encoding="utf-8")
    tree = ast.parse(code)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Filter")
    inlet = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "inlet")
    assert {"body", "__user__", "__metadata__", "__event_emitter__"} <= {a.arg for a in inlet.args.args}


def test_unsourced_amounts_are_flagged():
    sources = ("Le montant du contrat est augmenté de 12 500 000,00 DA HT, portant le montant total "
               "à 137 500 000,00 DA HT. Montant TTC 148 750 000,00 DA. TVA 19 %.")
    reponse = ("L'avenant est de 12 500 000,00 DA HT, soit 14 250 000,00 DA TTC ; "
               "total 148 750 000,00 DA TTC ; ou 12,500,000.00 DA ; signé en 2026.")
    assert presentation.montants_non_sources(reponse, sources) == ["14 250 000,00", "2026"]
    assert presentation.montants_non_sources("Pénalité de 1/1000, plafond 10 %.",
                                             "pénalité de 1/1000 par jour, plafonnée à 10 %") == []
    assert presentation.montants_non_sources("Pénalité de 1/1000.", sources) == ["1000"]


def test_output_control_uses_conversation_context():
    import asyncio

    from contract_rag.owui import adapters

    adapters._CONTEXTES["chat-1"] = "Montant : 125 000 000,00 DA"
    assert adapters.controle_reponse("Le montant est de 125 000 000,00 DA.", "chat-1") is None
    alerte = adapters.controle_reponse("Le TTC est de 148 750 000,00 DA.", "chat-1")
    assert alerte and "148 750 000,00" in alerte
    assert adapters.controle_reponse("148 750 000,00", "inconnu") is None
    assert adapters.cle_conversation({"chat_id": "c"}, {}) == "c"
    assert adapters.cle_conversation({}, {"chat_id": "m"}) == "m"
    ctx = asyncio.run(adapters.resolve_user({"id": "u", "email": "A@B.dz", "groups": ["G"]}))
    assert ctx.groups == ("G",) and ctx.email == "a@b.dz"


def test_tool_file_is_thin_and_documented():
    code = (ROOT / "owui_extensions/tools/contrats_recherche.py").read_text(encoding="utf-8")
    meta = parse_frontmatter(code)
    assert meta["title"].startswith("Contrats")
    assert "requirements" not in meta
    tree = ast.parse(code)
    tools = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Tools")
    methods = {n.name: n for n in tools.body if isinstance(n, ast.AsyncFunctionDef)}
    assert set(methods) == {"rechercher_dans_contrats", "rechercher_contrats", "fiche_contrat"}
    for method in methods.values():
        doc = ast.get_docstring(method)
        assert doc and ":param" in doc
        assert any(a.arg == "__user__" for a in method.args.args)
