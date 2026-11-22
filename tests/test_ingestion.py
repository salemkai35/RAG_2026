from pathlib import Path

import pytest

from contract_rag.ingestion.classify import classify_filename
from contract_rag.ingestion.loaders.excel import parse_xlsx
from contract_rag.ingestion.loaders.word import parse_docx
from contract_rag.ingestion.pipeline import embedding_text, read_manifest
from contract_rag.ingestion.structure import build_chunks, detect_heading, table_to_markdown
from contract_rag.ingestion.types import Block


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Avenant n°2 - IAP-EP-2023-014.pdf", ("avenant", 2)),
        ("AVENANT_03_signé.pdf", ("avenant", 3)),
        ("ODS 01 démarrage.pdf", ("ordre_service", 1)),
        ("Ordre de service n° 4 arrêt.pdf", ("ordre_service", 4)),
        ("PV Réception Provisoire.pdf", ("pv_reception_provisoire", None)),
        ("PVRD lot 2.pdf", ("pv_reception_definitive", None)),
        ("Levée des réserves.docx", ("levee_reserves", None)),
        ("Mainlevée caution bonne exécution.pdf", ("mainlevee_garantie", None)),
        ("Caution de bonne exécution.pdf", ("garantie", None)),
        ("BPU.xlsx", ("bordereau_prix", None)),
        ("CPS marché.pdf", ("cahier_charges", None)),
        ("Contrat IAP-EP-2023-014.pdf", ("contrat", None)),
        ("Marché de travaux.pdf", ("contrat", None)),
        ("Annexe 3 plans.pdf", ("annexe", None)),
        ("scan_0012.pdf", ("autre", None)),
    ],
)
def test_classify(name, expected):
    c = classify_filename(name)
    assert (c.type_document, c.numero_ordre) == expected


@pytest.mark.parametrize(
    "line,expected",
    [
        ("ARTICLE 12 : PÉNALITÉS DE RETARD", (1, "ARTICLE 12 : PÉNALITÉS DE RETARD")),
        ("Article 12.3 – Plafond des pénalités", (2, "Article 12.3 – Plafond des pénalités")),
        ("Article premier - Objet du contrat", (1, "Article premier - Objet du contrat")),
        ("CHAPITRE II - CONDITIONS FINANCIÈRES", (0, "CHAPITRE II - CONDITIONS FINANCIÈRES")),
        ("5.2 Délais d'exécution", (2, "5.2 Délais d'exécution")),
        ("Article 12 du présent contrat prévoit que", None),
        ("1.500.000,00 DA hors taxes", None),
        ("3.1 Le titulaire doit remettre les plans.", None),
        ("Le présent article fixe les délais", None),
    ],
)
def test_detect_heading(line, expected):
    assert detect_heading(line) == expected


def test_build_chunks_sections_pages_and_tables():
    blocks = [
        Block("text", "CONTRAT N° X\nCHAPITRE I - DISPOSITIONS GÉNÉRALES\nArticle 1 : Objet\nTravaux de réhabilitation.", page=1),
        Block("text", "Article 2 : Montant\nLe montant HT est fixé à 125 000 000,00 DA.\nCHAPITRE II - DÉLAIS\n"
                      "Article 3 : Délai\nLe délai est de 240 jours.", page=2),
        Block("table", page=2, table_ref="p2-t1", rows=[["Désignation", "Montant"], ["Lot 1", "100"]]),
    ]
    chunks = build_chunks(blocks, max_chars=500, overlap_chars=50)
    by_title = {c.section_titre: c for c in chunks if c.type_chunk == "texte"}
    assert by_title["Article 1 : Objet"].section_chemin == "CHAPITRE I - DISPOSITIONS GÉNÉRALES > Article 1 : Objet"
    assert by_title["Article 2 : Montant"].page_debut == 2
    assert "125 000 000,00" in by_title["Article 2 : Montant"].contenu
    assert by_title["Article 3 : Délai"].section_chemin == "CHAPITRE II - DÉLAIS > Article 3 : Délai"
    table = [c for c in chunks if c.type_chunk == "tableau"][0]
    assert table.table_ref == "p2-t1" and table.page_debut == 2
    assert table.contenu.startswith("| Désignation | Montant |")
    assert [c.ordre for c in chunks] == list(range(len(chunks)))


def test_long_article_split_with_overlap_and_page_range():
    lines = [f"Ligne {i} du cahier des charges avec du contenu." for i in range(60)]
    blocks = [
        Block("text", "Article 7 : Prescriptions\n" + "\n".join(lines[:30]), page=4),
        Block("text", "\n".join(lines[30:]), page=5),
    ]
    chunks = build_chunks(blocks, max_chars=600, overlap_chars=120)
    assert len(chunks) > 3
    assert all(len(c.contenu) <= 600 for c in chunks)
    assert all(c.section_titre == "Article 7 : Prescriptions" for c in chunks)
    assert any(c.page_debut == 4 and c.page_fin == 5 for c in chunks)
    # Chevauchement : la dernière ligne d'un chunk est reprise au début du suivant
    assert chunks[1].contenu.splitlines()[0] in chunks[0].contenu


def test_large_table_split_repeats_header():
    rows = [["Code", "Désignation", "Prix"]] + [[str(i), f"Article {i}", "10,00"] for i in range(80)]
    chunks = build_chunks([Block("table", rows=rows, table_ref="t")], max_chars=400, overlap_chars=50)
    assert len(chunks) > 1
    assert all(c.contenu.startswith("| Code | Désignation | Prix |") for c in chunks)


def test_table_markdown_escapes():
    assert table_to_markdown([["a|b", None], ["x\ny", "2"]]) == "| a\\|b |  |\n| --- | --- |\n| x y | 2 |"


def test_docx_loader(tmp_path: Path):
    import docx

    d = docx.Document()
    d.add_heading("Article 5 : Pénalités", level=1)
    d.add_paragraph("Une pénalité de 1/1000 par jour de retard est appliquée.")
    table = d.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Poste", "Montant"
    table.cell(1, 0).text, table.cell(1, 1).text = "Lot 1", "500"
    path = tmp_path / "avenant 1.docx"
    d.save(path)

    parsed = parse_docx(path)
    kinds = [b.kind for b in parsed.blocks]
    assert kinds == ["heading", "text", "table"]
    chunks = build_chunks(parsed.blocks)
    assert chunks[0].section_titre == "Article 5 : Pénalités"
    assert chunks[1].type_chunk == "tableau" and chunks[1].table_ref == "tableau-1"


def test_xlsx_loader_cell_references(tmp_path: Path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BPU"
    ws["B3"], ws["C3"], ws["D3"] = "N°", "Désignation", "Prix unitaire"
    for i in range(45):
        ws.cell(row=4 + i, column=2, value=i + 1)
        ws.cell(row=4 + i, column=3, value=f"Ouvrage {i + 1}")
        ws.cell(row=4 + i, column=4, value=1250.5)
    path = tmp_path / "BPU.xlsx"
    wb.save(path)

    parsed = parse_xlsx(path)
    assert [b.cell_ref for b in parsed.blocks] == ["'BPU'!B4:D43", "'BPU'!B44:D48"]
    assert parsed.blocks[0].rows[0] == ["N°", "Désignation", "Prix unitaire"]
    assert parsed.blocks[0].rows[1] == ["1", "Ouvrage 1", "1250.5"]


def test_manifest(tmp_path: Path):
    (tmp_path / "manifest.csv").write_text(
        "fichier;type_document;numero_ordre;date_document;remplace\n"
        "Avenant 2 signé.pdf;avenant;2;15/03/2024;Avenant 2 projet.pdf\n",
        encoding="utf-8",
    )
    entry = read_manifest(tmp_path)["avenant 2 signé.pdf"]
    assert entry.type_document == "avenant" and entry.numero_ordre == 2
    assert entry.remplace == "Avenant 2 projet.pdf"
    (tmp_path / "manifest.csv").write_text("fichier;type_document\nx.pdf;inconnu\n", encoding="utf-8")
    with pytest.raises(ValueError, match="inconnu"):
        read_manifest(tmp_path)


def test_embedding_text_has_context():
    chunk = build_chunks([Block("text", "Article 9 : Garanties\nCaution de 5 %.", page=1)])[0]
    text = embedding_text(chunk, "REF-1", "contrat", "Contrat.pdf")
    assert text.splitlines()[0] == "Contrat REF-1 | contrat | Contrat.pdf"
    assert "Article 9 : Garanties" in text and "Caution de 5 %." in text
